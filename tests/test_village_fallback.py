import copy

import pytest

from npc_sim import (village, village_communication, village_economy, village_extensions,
                     village_fallback, village_goods, village_life, village_social, village_turns,
                     village_wagon)
from npc_sim.contracts import observation
from npc_sim.living_village import build_scenario
from npc_sim.models import Scenario, VillageDayPolicy, VillageEconomyConfig
from npc_sim.village_communication_models import (NoticeKnowledge, NoticeTerms, NoticeVersion,
                                                 VillageCommunicationConfig, VillageCommunicationState)
from npc_sim.village_economy_models import VillageSupplierOrder
from npc_sim.world import World
from test_village_expanded import scenario
from test_world import act


def make_world(tmp_path, *, minute=3480, breakfast=False, enabled=True, living=False):
    value = scenario()
    if living:
        value.id = "millhaven-living"
    value.village.start_minute = minute
    value.village.anti_idle = enabled
    value.village.event_driven_waits = True
    value.village.breakfast = breakfast
    value.village.goal_deadlines = {}
    policy = village_economy.example_config(value)
    policy.update(reserve_targets={}, production_targets={"woods": {"timber": 2, "logs": 2}},
                  stock_buffer={}, meal_plans=[], require_orders=True)
    value.village.economy = VillageEconomyConfig.model_validate(policy)
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
    world = World(value, tmp_path / "fallback.json", "fallback", "rowan", "session-rowan")
    for agent in value.agents:
        world.bind(agent.id, f"session-{agent.id}")
    state = world.state.village
    state.breakdown = True
    world.state.entities[value.village.mill_equipment].health = 0
    for stock in state.stocks.values():
        for product in stock:
            stock[product] = 0
    state.stock_lots = {}
    village_goods.put(world, "woods", "timber", 20)
    village_goods.put(world, "woods", "logs", 20)
    return world


def inspect_then_wait(world):
    assert act(world, "village_observe")["code"] == "LOCAL_TOOLS_CHECKED"
    village.advance(world, 5)
    result = act(world, "village_wait_event")
    assert result["code"] == "EVENT_WAIT_STARTED", result
    return world.state.village.residents["rowan"].fallback.wait


def test_iona_prefers_garden_during_downtime_and_harvests_before_retending(tmp_path):
    value = build_scenario(scenario(), purposeful_downtime=True)
    value.village.start_minute = 600
    value.village.breakfast = False
    value.village.events = None
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
    world = World(value, tmp_path / "garden.json", "garden", "iona", "session-iona")
    for agent in value.agents:
        world.bind(agent.id, f"session-{agent.id}")
    state = world.state.village
    site = world.actor.location_id
    village_goods.put(world, site, "manure", 1)

    def garden_choices():
        return [choice["task_id"] for choice in village_fallback.candidates(world)
                if choice["task_id"] in {"garden:harvest", "leisure:herb-garden-care"}]

    assert garden_choices() == ["leisure:herb-garden-care"]
    state.social.garden_yields[site] = state.minute + 5
    assert garden_choices() == []
    assert village_social.observation(world)["social"]["activities"]["herb-garden-care"]["reason"] == "GARDEN_YIELD_PENDING"
    state.social.garden_yields[site] = state.minute
    assert garden_choices() == ["garden:harvest"]


def test_innkeeper_can_request_tomorrow_food_at_physical_board(tmp_path):
    value = build_scenario(scenario(), purposeful_downtime=True)
    value.village.start_minute = 1200
    value.village.events = None
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
    world = World(value, tmp_path / "food.json", "food", "elsie", "session-elsie")
    for agent in value.agents:
        world.bind(agent.id, f"session-{agent.id}")
    state = world.state.village
    state.coins["elsie"] = 1000
    for site, stock in state.stocks.items():
        for product in stock:
            stock[product] = 0
    state.stock_lots = {}
    village_goods.put(world, "bakery", "flour", 2)
    board = state.communication.config.board_id
    world.actor.location_id = world.state.entities[board].location
    request = next(choice for choice in village_fallback.candidates(world)
                   if choice["action_id"] == "village_request_supply"
                   and choice["arguments"]["good"] == "bread")
    assert 1200 <= request["arguments"]["deadline"] <= 1320
    assert request["arguments"]["required_fresh_until"] // 1440 == 1
    assert act(world, request["action_id"], **request["arguments"])["outcome"] == "APPLIED"
    assert not any(choice["action_id"] == "village_request_supply"
                   and choice["arguments"]["good"] == "bread"
                   for choice in village_fallback.candidates(world))


def test_food_request_explores_configured_supplier_after_a_known_attempt(tmp_path):
    value = build_scenario(scenario(), purposeful_downtime=True)
    value.village.start_minute = 1200
    value.village.events = None
    policy = value.village.economy
    primary = policy.suppliers["bread"]
    policy.supplier_alternatives = {"bread": ["mara"]}
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
    world = World(value, tmp_path / "suppliers.json", "suppliers", "elsie", "session-elsie")
    state = world.state.village
    state.coins["elsie"] = 1000
    for stock in state.stocks.values():
        stock.update(dict.fromkeys(stock, 0))
    state.stock_lots = {}
    board = state.communication.config.board_id
    world.actor.location_id = world.state.entities[board].location

    def bread_requests():
        return [choice["arguments"] for choice in village_fallback.candidates(world)
                if choice["action_id"] == "village_request_supply" and choice["arguments"]["good"] == "bread"]

    first, = bread_requests()
    assert first["preferred_supplier_id"] == primary
    assert act(world, "village_request_supply", **first)["code"] == "NOTICE_POSTED"
    assert not bread_requests()
    supplier = world.bind(primary, f"session-{primary}")
    supplier.actor.location_id = world.actor.location_id
    nid = next(iter(state.communication.notices))
    assert village_communication.apply(supplier, "village_notice_read", {"notice_ids": [nid]})[0] == "APPLIED"
    village.advance(world, 5)
    assert act(supplier, "village_respond_to_request", request_id=nid, response="reject",
               reason="No bread available")["code"] == "NOTICE_UPDATED"
    village.advance(world, 5)
    assert not bread_requests()
    village.advance(world, 5)
    alternate, = bread_requests()
    assert alternate == {**first, "preferred_supplier_id": "mara"}
    assert act(world, "village_request_supply", **alternate)["code"] == "NOTICE_POSTED"
    assert not bread_requests()
    second_id = next(reversed(state.communication.notices))
    second_supplier = world.bind("mara", "session-mara")
    second_supplier.actor.location_id = world.actor.location_id
    assert village_communication.apply(second_supplier, "village_notice_read", {"notice_ids": [second_id]})[0] == "APPLIED"
    village.advance(world, 5)
    assert act(second_supplier, "village_respond_to_request", request_id=second_id, response="reject",
               reason="No bread available")["code"] == "NOTICE_UPDATED"
    village.advance(world, 15)
    assert all(request["purpose"] != first["purpose"] for request in bread_requests())


def test_blocked_bakery_order_prompts_upstream_flour_request_at_board(tmp_path):
    value = build_scenario(scenario(), purposeful_downtime=True)
    value.village.start_minute = 600
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
    world = World(value, tmp_path / "upstream.json", "upstream", "jory", "session-jory")
    state = world.state.village
    board = state.communication.config.board_id
    world.actor.location_id = world.state.entities[board].location
    state.economy.orders["bread-short"] = VillageSupplierOrder(
        id="bread-short", request_key="bread-short", buyer="elsie", supplier="jory",
        source="bakery", destination="tavern", product="bread", quantity=4,
        price=3, deadline=1050, required_fresh_until=1140, created_at=600,
        status="deferred", reason="SUPPLY_NOT_READY")
    state.stocks["bakery"]["flour"] = 0
    state.stock_lots.get("bakery", {}).pop("flour", None)
    state.stocks["bakery"]["bread"] = 0
    state.stock_lots.get("bakery", {}).pop("bread", None)
    state.coins["jory"] = 100
    request = next(choice for choice in village_fallback.candidates(world)
                   if choice["task_id"].startswith("production:request:")
                   and choice["action_id"] == "village_request_supply")
    assert request["arguments"]["good"] == "flour"
    assert request["arguments"]["deadline"] > state.minute
    result = act(world, request["action_id"], **request["arguments"])
    assert result["outcome"] == "APPLIED", result
    assert not any(choice["task_id"].startswith("production:request:")
                   for choice in village_fallback.candidates(world))


def test_funded_grain_notice_is_prioritized_over_optional_research(tmp_path):
    value = build_scenario(scenario(), purposeful_downtime=True)
    value.village.start_minute = 600
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
    world = World(value, tmp_path / "grain-reply.json", "grain-reply", "mara", "session-mara")
    state = world.state.village
    farmer = world.bind("ada", "session-ada")
    world.actor.location_id = farmer.actor.location_id = "farm"
    state.coins["mara"] = 100
    result = act(world, "village_request_supply", good="grain", quantity=1,
                 purpose="Milling flour for a paid bakery order", deadline=900,
                 required_fresh_until=960, preferred_supplier_id="ada")
    assert result["outcome"] == "APPLIED", result
    village.advance(world, 5)
    reply = next(choice for choice in village_fallback.candidates(farmer)
                 if choice["action_id"] == "village_respond_to_request")
    assert reply["priority"] == "P1" and reply["arguments"]["response"] == "accept"
    assert act(farmer, reply["action_id"], **reply["arguments"])["outcome"] == "APPLIED"
    assert next(iter(state.economy.orders.values())).status in {"accepted", "accepted_planned"}


def test_courier_dispatches_only_for_confirmed_funded_shipment(tmp_path):
    value = build_scenario(scenario(), purposeful_downtime=True)
    value.village.start_minute = 600
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
    world = World(value, tmp_path / "wagon-priority.json", "wagon-priority", "nella", "session-nella")
    state = world.state.village
    state.wagon.repaired = state.wagon.hitched = True
    world.actor.location_id = world.state.entities["wagon"].location
    state.residents["nella"].life.meals["0:breakfast"].status = "eaten"
    assert not [choice for choice in village_fallback.candidates(world)
                if choice["task_id"] == "shipment:wagon"]
    state.economy.orders["wagon-bread"] = VillageSupplierOrder(
        id="wagon-bread", request_key="wagon-bread", buyer="elsie", supplier="jory",
        source="bakery", destination="tavern", product="bread", quantity=2,
        price=1, deadline=900, required_fresh_until=850, created_at=600,
        status="ready_to_collect")
    village_goods.put(world, "bakery", "bread", 2)
    assert village_wagon.readiness(world)["ready"]
    choice = next(choice for choice in village_fallback.candidates(world)
                  if choice["task_id"] == "shipment:wagon")
    assert choice["priority"] == "P1"
    assert act(world, choice["action_id"], **choice["arguments"])["code"] == "ROUTE_STARTED"
    for _ in range(60):
        if not state.wagon.automatic:
            break
        state.minute = state.wagon.next_event_at
        village_wagon.advance(world)
    assert state.economy.orders["wagon-bread"].status == "fulfilled"
    assert state.economy.orders["wagon-bread"].paid > 0
    assert state.wagon.loads == []
    assert world.state.entities["wagon"].location == "forge"


def test_nella_prioritizes_known_feasible_freight_before_store_prospecting(tmp_path):
    value = build_scenario(scenario(), purposeful_downtime=True)
    value.village.start_minute = 600
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
    world = World(value, tmp_path / "freight.json", "freight", "nella", "session-nella")
    for agent in value.agents:
        world.bind(agent.id, f"session-{agent.id}")
    state = world.state.village
    state.residents["nella"].life.meals["0:breakfast"].status = "eaten"
    state.economy.orders["known-bread"] = VillageSupplierOrder(
        id="known-bread", request_key="known-bread", buyer="elsie", supplier="jory",
        source="bakery", destination="tavern", product="bread", quantity=2,
        price=3, delivery_fee=1, deadline=900, required_fresh_until=1140,
        created_at=600, status="ready_to_collect")
    village_goods.put(world, "bakery", "bread", 2)
    choice = next(item for item in village_fallback.candidates(world)
                  if item["task_id"] == "freight:collect:known-bread")
    assert choice["priority"] == "P1"
    assert act(world, choice["action_id"], **choice["arguments"])["outcome"] == "APPLIED"
    village.advance(world, 5)
    world.actor.location_id = "bakery"
    choice = next(item for item in village_fallback.candidates(world)
                  if item["task_id"] == "freight:load:known-bread")
    assert choice["arguments"] == {"goods": {"bread": 2}, "destination": "tavern"}
    assert act(world, choice["action_id"], **choice["arguments"])["outcome"] == "APPLIED"


def test_confirmed_crew_meeting_is_prioritized_over_optional_work(tmp_path):
    value = build_scenario(scenario(), purposeful_downtime=True)
    value.village.start_minute = 600
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
    world = World(value, tmp_path / "crew-priority.json", "crew-priority", "mara", "session-mara")
    state = world.state.village
    world.actor.location_id = "village-square"
    notice = NoticeVersion(
        id="crew-confirmed", version=2, author="hobb", created_at=550, updated_at=590,
        status="accepted", terms=NoticeTerms(
            type="meeting", subject="repair_mill", description="Repair the mill",
            recipients=["mara"], destination="watermill", due_at=630),
        appointment=village_communication.Appointment(
            id="crew-confirmed", version=2, project="repair_mill",
            members=["hobb", "mara"], accepted=["hobb", "mara"],
            place="watermill", due_at=630, end_at=690, status="confirmed"))
    state.communication.notices[notice.id] = [notice]
    state.communication.knowledge.setdefault("mara", {})[notice.id] = NoticeKnowledge(
        snapshot=notice, heard_at=590, source_type="conversation")
    choice = next(choice for choice in village_fallback.candidates(world)
                  if choice["task_id"] == "crew:attend:crew-confirmed")
    assert choice["priority"] == "P1" and choice["destination"] == "watermill"
    state.minute = 695
    assert not any(choice["task_id"] == "crew:attend:crew-confirmed"
                   for choice in village_fallback.candidates(world))


def test_current_tasks_skips_order_not_yet_known_to_buyer(tmp_path):
    world = make_world(tmp_path)
    buyer = world.bind("finn", "session-finn")
    state = world.state.village
    state.communication = VillageCommunicationState(config=VillageCommunicationConfig())
    order = VillageSupplierOrder(
        id="order-1", request_key="notice:notice-1", buyer="finn", supplier="rowan",
        source="woods", destination="riverbank", product="logs", quantity=1,
        price=1, deadline=state.minute + 60, required_fresh_until=state.minute + 60,
        created_at=state.minute, status="accepted", coordination_notice_id="notice-1")
    state.economy.orders[order.id] = order
    notice = NoticeVersion(
        id="notice-1", version=1, author="finn", created_at=state.minute,
        updated_at=state.minute, terms=NoticeTerms(
            type="supply_request", subject="logs", description="Need logs",
            recipients=["rowan"]))
    state.communication.knowledge.setdefault("finn", {})[notice.id] = NoticeKnowledge(
        snapshot=notice, heard_at=state.minute, source_type="author")

    assert not any(item["task"] == order.id for items in village_fallback.current_tasks(buyer).values()
                   for item in items)
    state.communication.knowledge["finn"][notice.id].snapshot.order_snapshot = order.model_dump(mode="json")
    assert any(item["task"] == order.id and item["status"] == "accepted"
               for item in village_fallback.current_tasks(buyer)["commitments"])


def test_finn_preservation_requires_funded_physical_log_and_uncommitted_fish(tmp_path):
    base = scenario()
    base.village.start_minute = 600
    value = build_scenario(base, purposeful_downtime=True)
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
    world = World(value, tmp_path / "smoking.json", "smoking", "finn", "session-finn")
    finn = world
    rowan = world.bind("rowan", "session-rowan")
    state = world.state.village
    for rid in ("finn", "rowan"):
        state.residents[rid].life.last_woke = 420
    assert state.stocks["riverbank"]["logs"] == 0
    assert not [item for item in village_fallback.candidates(finn)
                if item["task_id"] == "preservation:smoke-fish"]
    assert village_economy.production_targets(finn, "riverbank")["fish"] == 0
    assert [item["task_id"] for item in village_fallback.candidates(finn)
            if item["task_id"].startswith("preservation:")] == ["preservation:post-log-request"]

    # Follow real doors to Rowan's physical inbox; posting does not notify him.
    while finn.actor.location_id != "woods":
        step = next(item for item in village_fallback.candidates(finn)
                    if item["task_id"] == "preservation:post-log-request")
        result = act(finn, step["action_id"], **step["arguments"])
        assert result["outcome"] == "APPLIED", result
        village.advance(finn, 5)
    request = next(item for item in village_fallback.candidates(finn)
                   if item["task_id"] == "preservation:request-log")
    assert act(finn, request["action_id"], **request["arguments"])["outcome"] == "APPLIED"
    assert state.economy.orders == {}
    if not state.communication.knowledge.get("rowan"):
        assert state.communication.placements["workplace-inbox-rowan"]
    village.advance(finn, 5)
    if not state.communication.knowledge.get("rowan"):
        assert act(rowan, "village_check_messages", channel="inbox")["outcome"] == "APPLIED"
    village.advance(finn, 5)
    accepted = next(item for item in village_fallback.candidates(rowan)
                    if item["task_id"] == "preservation:accept-log")
    assert act(rowan, accepted["action_id"], **accepted["arguments"])["outcome"] == "APPLIED"
    order = next(iter(state.economy.orders.values()))
    assert (order.product, order.buyer, order.supplier, order.destination) == (
        "logs", "finn", "rowan", "riverbank")

    village.advance(finn, 5)
    load = next(item for item in village_fallback.candidates(rowan)
                if item["task_id"] == "preservation:load-log")
    before_coins = state.coins.copy()
    before_woods = state.stocks["woods"]["logs"]
    assert act(rowan, load["action_id"], **load["arguments"])["outcome"] == "APPLIED"
    assert state.stocks["woods"]["logs"] == before_woods - 1
    assert state.stocks["riverbank"]["logs"] == 0
    assert state.coins == before_coins
    while rowan.actor.location_id != "riverbank":
        village.advance(finn, max(5, state.residents["rowan"].available_at - state.minute))
        step = next(item for item in village_fallback.candidates(rowan)
                    if item["task_id"] == "preservation:deliver-log")
        assert act(rowan, step["action_id"], **step["arguments"])["outcome"] == "APPLIED"
    village.advance(finn, max(5, state.residents["rowan"].available_at - state.minute))
    unload = next(item for item in village_fallback.candidates(rowan)
                  if item["task_id"] == "preservation:unload-log")
    assert act(rowan, unload["action_id"], **unload["arguments"])["outcome"] == "APPLIED"
    assert order.status == "fulfilled" and state.stocks["riverbank"]["logs"] == 1
    assert state.coins["finn"] == before_coins["finn"] - order.price - order.delivery_fee
    assert state.coins["rowan"] == before_coins["rowan"] + order.price + order.delivery_fee
    assert not [item for item in village_fallback.candidates(finn)
                if item["task_id"] == "preservation:request-log"]

    # Fish remain real and protected until outstanding food commitments are met.
    assert village_economy.production_targets(finn, "riverbank")["fish"] >= 2
    finn.actor.location_id = "riverbank"
    village_goods.put(finn, "riverbank", "fish", 2)
    state.residents["finn"].life.meals["0:breakfast"].status = "eaten"
    state.residents["finn"].life.hungry = False
    state.residents["finn"].available_at = state.minute
    assert [item for item in village_fallback.candidates(finn)
            if item["task_id"] == "preservation:smoke-fish"], (
                state.minute, village_social.observation(finn)["social"]["activities"]["fish-smoking"]["reason"],
                village_fallback.candidates(finn))
    start = act(finn, "village_leisure", activity="fish-smoking", companions=[])
    assert start["outcome"] == "APPLIED", start
    assert state.stocks["riverbank"]["fish"] == 0 and state.stocks["riverbank"]["logs"] == 0
    village.advance(finn, 45)
    assert state.stocks["riverbank"]["preserved-fish"] == 1
    paid = len(state.economy.payments)
    assert act(rowan, "village_unload_goods")["outcome"] == "REJECTED"
    assert len(state.economy.payments) == paid
    finn.save()
    restored = World(value, finn.path, "smoking", "finn", "session-finn")
    assert restored.state.village.stocks["riverbank"]["preserved-fish"] == 1


def test_daytime_food_requires_surplus_and_sleep_waits_for_bedtime(tmp_path):
    world = make_world(tmp_path, minute=1080, living=True)
    world.actor.location_id = "tavern"
    village_goods.put(world, "tavern", "bread", len(world.scenario.village.residents))
    assert not any(choice["action_id"] == "village_eat" for choice in village_fallback.candidates(world))
    village_goods.put(world, "tavern", "bread", 1)
    assert any(choice["action_id"] == "village_eat" for choice in village_fallback.candidates(world))
    assert act(world, "village_eat")["code"] == "MEAL_EATEN"
    assert world.state.village.residents["rowan"].life.meals[f"{world.state.village.minute // 1440}:daytime"].status == "eaten"
    assert not any(choice["action_id"] == "village_eat" for choice in village_fallback.candidates(world))
    village.advance(world, world.state.village.residents["rowan"].available_at - world.state.village.minute)
    world.actor.location_id = world.scenario.village.residents["rowan"].home
    assert not any(choice["action_id"] == "village_sleep" for choice in village_fallback.candidates(world))
    assert act(world, "village_sleep")["code"] == "REST_NOT_DUE"
    village.advance(world, world.scenario.village.residents["rowan"].bed_minute - world.state.village.minute)
    assert any(choice["action_id"] == "village_sleep" for choice in village_fallback.candidates(world))
    assert act(world, "village_sleep")["code"] == "SLEEP_STARTED"


def test_rowan_day_three_empty_breakfast_is_attempted_once_and_penalty_is_not_duplicated(tmp_path):
    world = make_world(tmp_path, minute=3330, breakfast=True)
    world.actor.location_id = "tavern"
    life = world.state.village.residents["rowan"].life
    first = act(world, "village_eat")
    assert first["code"] == "MEAL_MISSED"
    village.advance(world, 5)
    world.state.entities["tavern-hearth"].description += " A different local observation."
    for _ in range(3):
        assert act(world, "village_eat")["code"] == "RETRY_SUPPRESSED"
    assert life.meals["2:breakfast"].status == "missed"
    assert life.missed_meals == 1
    village.advance(world, 100)
    assert life.missed_meals == 1
    assert sum(event.action == "missed_meal" and event.actor == "rowan"
               for event in world.state.village.events) == 1
    block = next(iter(world.state.village.residents["rowan"].fallback.blocked.values()))
    assert block.engine_code == "MEAL_MISSED" and block.attempt_count_since_change == 4


def test_real_local_food_reopens_meal_attempt_and_preserves_existing_late_recovery(tmp_path):
    world = make_world(tmp_path, minute=3330, breakfast=True)
    world.actor.location_id = "tavern"
    assert act(world, "village_eat")["code"] == "MEAL_MISSED"
    village.advance(world, 5)
    coins = world.state.village.coins["rowan"]
    village_goods.put(world, "tavern", "bread", 1)
    assert act(world, "village_eat")["code"] == "MEAL_EATEN"
    life = world.state.village.residents["rowan"].life
    assert life.meals["2:breakfast"].status == "eaten"
    assert life.missed_meals == 0
    assert world.state.village.coins["rowan"] < coins
    assert world.state.village.stocks["tavern"]["bread"] == 0


def test_unfunded_food_is_not_an_eligible_meal_and_funding_changes_reopen_the_check(tmp_path):
    world = make_world(tmp_path, minute=3330, breakfast=True)
    world.actor.location_id = "tavern"
    state = world.state.village
    village_goods.put(world, "tavern", "bread", 1)
    state.coins["rowan"] = 0
    assert not any(choice["action_id"] == "village_eat" for choice in village_fallback.candidates(world))
    assert act(world, "village_eat")["code"] == "MEAL_MISSED"
    block = next(iter(state.residents["rowan"].fallback.blocked.values()))
    assert block.reason_code == "PAYMENT_INSUFFICIENT"
    assert state.stocks["tavern"]["bread"] == 1
    village.advance(world, 5)
    state.coins["rowan"] = 10
    assert act(world, "village_eat")["code"] == "MEAL_EATEN"


def test_innkeeper_arrival_reopens_blocked_meal(tmp_path):
    world = make_world(tmp_path, minute=3330, breakfast=True)
    world.actor.location_id = "tavern"
    village_goods.put(world, "tavern", "bread", 1)
    world.state.actors["elsie"].location_id = "cottages"
    assert not any(choice["action_id"] == "village_eat" for choice in village_fallback.candidates(world))
    assert act(world, "village_eat")["code"] == "INN_NOT_OPEN"
    village.advance(world, 5)
    assert act(world, "village_eat")["code"] == "RETRY_SUPPRESSED"
    world.state.actors["elsie"].location_id = "tavern"
    assert any(choice["action_id"] == "village_eat" for choice in village_fallback.candidates(world))
    assert act(world, "village_eat")["code"] == "MEAL_MISSED"
    assert any(block.engine_code == "MEAL_MISSED" for block in world.state.village.residents["rowan"].fallback.blocked.values())


def test_resolved_breakfast_cutoff_does_not_prevent_legitimate_waiting(tmp_path):
    world = make_world(tmp_path, minute=3330, breakfast=True)
    world.actor.location_id = "tavern"
    assert act(world, "village_eat")["code"] == "MEAL_MISSED"
    village.advance(world, 90)
    assert act(world, "village_wait_event")["code"] == "ELIGIBLE_TASK_PENDING"
    assert any(choice["task_id"] == "return:workplace" for choice in village_fallback.candidates(world))


def test_event_wait_intent_skips_optional_leisure_and_stops_before_breakfast(tmp_path):
    world = make_world(tmp_path, minute=3330, breakfast=True)
    world.actor.location_id = "tavern"
    person = world.state.village.residents["rowan"]
    assert any(action["id"] == "wait_until_next_relevant_event" for action in world.actions())
    result = act(world, "wait_until_next_relevant_event")
    assert result["code"] == "EVENT_WAIT_STARTED", result
    assert person.available_at <= min(village_fallback._obligation_limits(world))
    assert person.fallback.wait is not None


def test_pending_local_maintenance_prevents_an_idle_wait(tmp_path):
    from test_village_maintenance import make_world as maintenance_world

    world = maintenance_world(tmp_path)
    world.scenario.village.anti_idle = True
    state = world.state.village
    state.stocks["forge"]["iron"] = 0
    job = next(iter(state.maintenance.jobs))
    choices = village_fallback.candidates(world)
    assert any(choice["action_id"] == "village_maintain" and choice["arguments"] == {"job": job}
               for choice in choices)
    assert act(world, "village_wait_event")["code"] == "ELIGIBLE_TASK_PENDING"
    assert act(world, "village_maintain", job=job)["outcome"] == "APPLIED"


def test_idle_elsie_returns_to_tavern_before_waiting(tmp_path):
    world = make_world(tmp_path, minute=390, living=True)
    elsie = world.bind("elsie", "session-elsie")
    elsie.actor.location_id = "village-square"
    person = elsie.state.village.residents["elsie"]

    choice = next(choice for choice in village_fallback.candidates(elsie)
                  if choice["task_id"] == "return:workplace")
    assert choice["action_id"] == "village_plan_walk"
    assert choice["arguments"]["destination"] == "tavern"
    assert act(elsie, "village_wait_event")["code"] == "ELIGIBLE_TASK_PENDING"
    assert act(elsie, "village_plan_walk", **choice["arguments"])["code"] == "ROUTE_PLANNED"
    village.advance(elsie, 5)

    step = next(choice for choice in village_fallback.candidates(elsie)
                if choice["task_id"] == "return:workplace")
    assert step["action_id"] == "go"
    assert act(elsie, "village_wait_event")["code"] == "ELIGIBLE_TASK_PENDING"
    assert act(elsie, "go", **step["arguments"])["outcome"] == "APPLIED"
    village.advance(elsie, 5)
    assert elsie.actor.location_id == "tavern"
    assert person.walking_plan is None
    assert not any(choice["task_id"] == "return:workplace" for choice in village_fallback.candidates(elsie))


def test_idle_return_does_not_override_another_committed_route(tmp_path):
    world = make_world(tmp_path, minute=540, living=True)
    world.actor.location_id = "village-square"
    assert act(world, "village_plan_walk", destination="tavern", purpose="Visit the tavern")["code"] == "ROUTE_PLANNED"
    village.advance(world, 5)
    assert not any(choice["task_id"] == "return:workplace" for choice in village_fallback.candidates(world))


def test_arriving_early_for_a_meal_allows_waiting_until_the_meal_is_due(tmp_path):
    world = make_world(tmp_path, minute=4015)
    world.actor.location_id = "tavern"
    assert act(world, "village_wait_event")["code"] == "EVENT_WAIT_STARTED"
    assert world.state.village.residents["rowan"].available_at == 4020


def test_five_minute_wait_at_confirmed_meeting_site_is_legal(tmp_path):
    world = make_world(tmp_path, minute=600)
    person = world.state.village.residents["rowan"]
    person.life.urgent_project = "dig_well"
    person.life.urgent_until = world.state.village.minute + 5
    world.actor.location_id = world.scenario.village.well_site
    result = act(world, "village_wait", minutes=5)
    assert result["outcome"] == "APPLIED", result


def test_capped_timber_and_logs_record_specific_blocks_without_creating_stock(tmp_path):
    world = make_world(tmp_path)
    before = copy.deepcopy(world.state.village.stocks)
    for action in ("village_work", "village_fell_logs"):
        assert act(world, action)["code"] == "DEMAND_MET"
        world.state.revision += 1
        village.advance(world, 5)
        assert act(world, action)["code"] == "RETRY_SUPPRESSED"
    assert world.state.village.stocks == before
    blocks = world.state.village.residents["rowan"].fallback.blocked
    assert {block.reason_code for block in blocks.values()} == {"STOCK_POLICY_PROHIBITS_PRODUCTION"}
    assert len([event for event in world.state.village.events if event.action == "TASK_BLOCKED"]) == 2


def test_inspection_is_real_bounded_and_never_improves_tools_or_produces_goods(tmp_path):
    world = make_world(tmp_path)
    state = world.state.village
    candidates = world.observe()["village"]["fallback"]["eligible_candidates"]
    assert [item["action_id"] for item in candidates] == ["village_observe"]
    before = copy.deepcopy(state.stocks)
    tools = {key: entity.health for key, entity in world.state.entities.items()}
    assert act(world, "village_wait_event")["code"] == "ELIGIBLE_TASK_PENDING"
    assert act(world, "village_observe")["code"] == "LOCAL_TOOLS_CHECKED"
    assert state.residents["rowan"].available_at == state.minute + 5
    assert not any(event.action == "FALLBACK_COMPLETED" for event in state.events)
    village.advance(world, 5)
    assert act(world, "village_observe")["code"] == "INSPECTION_UNCHANGED"
    action = next(item for item in world.actions() if item["id"] == "village_observe")
    assert action["availability"] == "blocked"
    assert state.stocks == before
    assert {key: entity.health for key, entity in world.state.entities.items()} == tools
    assert {event.action for event in state.events} >= {
        "FALLBACK_SELECTED", "FALLBACK_STARTED", "FALLBACK_COMPLETED"}
    village.advance(world, 55)
    assert act(world, "village_observe")["code"] == "LOCAL_TOOLS_CHECKED"


def test_useful_inspection_becomes_eligible_again_when_a_visible_tool_changes(tmp_path):
    world = make_world(tmp_path)
    inspect_then_wait(world)
    tool = next(entity for entity in world.state.entities.values()
                if entity.location == "woods" and entity.kind != "door")
    tool.description += " A newly observed crack."
    village_extensions.advance(world)
    assert world.state.village.residents["rowan"].fallback.wait is None
    assert act(world, "village_observe")["code"] == "LOCAL_TOOLS_CHECKED"


def test_no_legal_work_allows_an_interruptible_bounded_wait_without_advancing_everyone(tmp_path):
    world = make_world(tmp_path)
    wait = inspect_then_wait(world)
    state = world.state.village
    assert state.minute == 3485
    assert wait.interruptible
    assert wait.planned_end == 3490
    assert wait.next_safety_review == wait.planned_end
    assert "local_stock_changed" in wait.wake_on
    assert state.residents["rowan"].available_at == wait.planned_end
    assert act(world, "village_wait", minutes=5)["code"] == "ACTOR_BUSY"
    village.advance(world, 5)
    assert state.residents["rowan"].fallback.wait is None
    assert state.residents["rowan"].available_at == wait.planned_end


def test_consecutive_event_waits_extend_to_ten_minutes_and_reset_after_work(tmp_path):
    world = make_world(tmp_path)
    inspect_then_wait(world)
    state = world.state.village
    person = state.residents["rowan"]
    village.advance(world, 5)
    assert state.minute == 3490
    assert person.fallback.wait is None
    assert act(world, "village_wait_event")["code"] == "EVENT_WAIT_STARTED"
    assert person.fallback.wait.planned_end == 3500
    assert person.available_at == 3500
    world.save()
    restored = World(world.scenario, world.path, "fallback", "rowan", "session-rowan")
    assert restored.state.village.residents["rowan"].fallback.last_wait_end == 3500
    village.advance(restored, 10)
    assert act(restored, "village_observe")["code"] == "INSPECTION_UNCHANGED"
    village.advance(restored, 50)
    assert act(restored, "village_observe")["code"] == "LOCAL_TOOLS_CHECKED"
    village.advance(restored, 5)
    assert act(restored, "village_wait_event")["code"] == "EVENT_WAIT_STARTED"
    assert restored.state.village.residents["rowan"].fallback.wait.planned_end == restored.state.village.minute + 5


def test_scheduler_skips_inert_period_without_repeated_model_turns(tmp_path):
    world = make_world(tmp_path)
    wait = inspect_then_wait(world)
    state = world.state.village
    for rid, person in state.residents.items():
        if rid != "rowan":
            person.life.sleeping = True
            person.life.sleep_until = state.minute + 300
    assert village_turns.next_actor(world) == "rowan"
    assert state.minute == wait.next_safety_review
    assert state.residents["rowan"].fallback.wait is None
    assert sum(event.action == "TASK_REEVALUATED" for event in state.events) == 1


def test_local_stock_change_interrupts_wait_but_hidden_remote_stock_does_not(tmp_path):
    world = make_world(tmp_path)
    wait = inspect_then_wait(world)
    state = world.state.village
    village_goods.put(world, "tavern", "bread", 1)
    village_extensions.advance(world)
    assert state.residents["rowan"].fallback.wait == wait
    village_goods.put(world, "woods", "logs", 1)
    village_extensions.advance(world)
    assert state.residents["rowan"].fallback.wait is None
    assert state.residents["rowan"].available_at == state.minute
    event = next(event for event in reversed(state.events) if event.action == "WAIT_INTERRUPTED")
    assert event.witnesses == ["rowan"]
    assert event.details["reason_code"] == "PERSONAL_CONTEXT_CHANGED"


def test_actual_nearby_speech_wakes_a_waiter_without_creating_an_order_or_free_production(tmp_path):
    world = make_world(tmp_path)
    world.state.actors["hobb"].location_id = "woods"
    inspect_then_wait(world)
    hobb = world.bind("hobb", "session-hobb")
    assert act(hobb, "say", message="I need timber for my workshop; let us arrange a real order.")["code"] == "SPOKEN"
    assert world.state.village.residents["rowan"].fallback.wait is None
    assert world.actor.messages[-1]["actor_id"] == "hobb"
    assert world.state.village.economy.orders == {}
    assert act(world, "village_work")["code"] == "DEMAND_MET"


def test_unchanged_failed_supplier_search_is_not_repeated(tmp_path):
    world = make_world(tmp_path)
    args = {"supplier": "jory", "product": "bread", "quantity": 1,
            "deadline": 3700, "required_fresh_until": 3800}
    assert act(world, "village_propose_order", **args)["code"] == "OUT_OF_REACH"
    assert act(world, "village_propose_order", **args)["code"] == "RETRY_SUPPRESSED"
    block = next(iter(world.state.village.residents["rowan"].fallback.blocked.values()))
    assert block.reason_code == "PERSON_UNAVAILABLE"
    assert world.actor.location_id == "woods"
    assert world.state.village.economy.orders == {}


def test_short_idle_wait_preserves_legacy_and_purposeful_modes(tmp_path):
    world = make_world(tmp_path)
    assert act(world, "village_observe")["outcome"] == "APPLIED"
    village.advance(world, 5)
    assert act(world, "village_wait", minutes=5)["code"] == "VILLAGE_WAITED"
    person = world.state.village.residents["rowan"]
    assert person.fallback.wait is None
    purposeful_world = make_world(tmp_path / "purposeful")
    assert act(purposeful_world, "village_observe")["outcome"] == "APPLIED"
    village.advance(purposeful_world, 5)
    village_extensions.configure(purposeful_world, "purposeful", purposeful_world.revision(),
                                 {"day_policy": VillageDayPolicy(anti_idle=True, purposeful_downtime=True).model_dump()})
    assert act(purposeful_world, "village_wait", minutes=5)["code"] == "EVENT_WAIT_STARTED"
    purposeful_person = purposeful_world.state.village.residents["rowan"]
    assert purposeful_person.fallback.wait is not None
    assert purposeful_person.available_at == purposeful_person.fallback.wait.planned_end


def test_due_needs_and_real_eligible_work_take_priority_over_fallback_inspection(tmp_path):
    world = make_world(tmp_path)
    state = world.state.village
    village_goods.take(world, "woods", "timber", 20)
    choices = village_fallback.candidates(world)
    assert choices[0]["action_id"] == "village_work"
    assert choices[0]["priority"] == "P3"
    assert act(world, "village_wait_event")["code"] == "ELIGIBLE_TASK_PENDING"
    before = state.stocks["woods"]["logs"]
    assert act(world, "village_work")["outcome"] == "APPLIED"
    assert state.stocks["woods"]["logs"] < before


def test_changed_stock_policy_revalidates_at_execution_and_does_not_bypass_resources(tmp_path):
    world = make_world(tmp_path)
    assert act(world, "village_work")["code"] == "DEMAND_MET"
    village_economy.config(world).production_targets["woods"]["timber"] = 100
    village_goods.take(world, "woods", "logs", 20)
    assert act(world, "village_work")["code"] == "STOCK_SHORTAGE"
    assert world.state.village.stocks["woods"]["timber"] == 20
    village_goods.put(world, "woods", "logs", 1)
    assert act(world, "village_work")["outcome"] == "APPLIED"
    assert world.state.village.stocks["woods"]["logs"] == 0


def test_meal_and_bedtime_bound_waits_with_real_travel_allowance(tmp_path):
    world = make_world(tmp_path, minute=4000)
    resident = world.scenario.village.residents["rowan"]
    path = village.route(world, "woods", "tavern")
    assert village_fallback.next_review(world) <= 4020 - 5 * len(path) - 5
    assert act(world, "village_wait_event")["code"] == "COMMITMENT_DUE"
    assert act(world, "village_observe")["code"] == "COMMITMENT_DUE"
    world.state.village.minute = 1440 * 2 + resident.bed_minute
    assert act(world, "village_wait_event")["code"] == "REST_DUE"


def test_retry_suppression_is_per_actor_and_survives_reload_and_request_replay(tmp_path):
    world = make_world(tmp_path)
    revision = world.revision()
    first = world.act("blocked-once", revision, "village_work", {})
    assert first["code"] == "DEMAND_MET"
    assert world.act("blocked-once", revision, "village_work", {}) == first
    restored = World(world.scenario, world.path, "fallback", "rowan", "session-rowan")
    assert act(restored, "village_work")["code"] == "RETRY_SUPPRESSED"
    assert restored.state.village.residents["hobb"].fallback.blocked == {}
    assert len([event for event in restored.state.village.events if event.action == "TASK_BLOCKED"]) == 1


def test_planning_is_private_and_contains_only_supported_actions_without_a_notice_plugin(tmp_path):
    world = make_world(tmp_path)
    assert world.state.village.communication is None
    assert act(world, "village_work")["code"] == "DEMAND_MET"
    rowan = observation(world.observe())["village"]["fallback"]
    assert rowan["blocked_tasks"][0]["retry_suppressed"]
    assert rowan["eligible_candidates"][0]["action_id"] == "village_observe"
    ids = {action["id"] for action in world.actions()}
    assert all(choice["action_id"] in ids for choice in rowan["eligible_candidates"])
    hobb = world.bind("hobb", "session-hobb").observe()["village"]["fallback"]
    assert hobb["blocked_tasks"] == []
    assert not any(event["action"] == "TASK_BLOCKED"
                   for event in world.bind("hobb", "session-hobb").observe()["village"]["events"])


def test_legacy_opt_out_and_configuration_validation(tmp_path):
    world = make_world(tmp_path, enabled=False)
    assert "fallback" not in world.observe()["village"]
    assert act(world, "village_wait", minutes=5)["code"] == "VILLAGE_WAITED"
    assert not world.state.village.residents["rowan"].fallback.blocked
    for interval in (0, 5, 14, 241):
        with pytest.raises(ValueError):
            VillageDayPolicy(idle_review_minutes=interval)


def test_purposeful_downtime_opt_in_bounds_wait_and_preserves_legacy_policy(tmp_path):
    world = make_world(tmp_path)
    assert not village_extensions.policy(world).purposeful_downtime
    inspect_then_wait(world)
    assert world.state.village.residents["rowan"].fallback.wait.planned_end == 3490
    patch = VillageDayPolicy(anti_idle=True, purposeful_downtime=True)
    village_extensions.configure(world, "purposeful", world.revision(), {"day_policy": patch.model_dump()})
    assert village_fallback.purposeful(world)
    assert village_extensions.policy(world).purposeful_downtime
    world.state.village.residents["rowan"].fallback.wait = None
    assert village_fallback.next_review(world) <= world.state.village.minute + 30
    world.save()
    restored = World(world.scenario, world.path, "fallback", "rowan", "session-rowan")
    assert village_fallback.purposeful(restored)
    with pytest.raises(ValueError, match="requires anti-idle"):
        village_extensions.configure(restored, "bad", restored.revision(),
                                     {"day_policy": VillageDayPolicy(purposeful_downtime=True).model_dump()})


def test_purposeful_board_visit_requires_real_trip_and_read_to_gain_knowledge(tmp_path):
    world = make_world(tmp_path, minute=3480)
    from npc_sim import village_communication
    village_communication.configure(world, {"board_id": "noticeboard", "workplace_boxes": True,
                                           "max_hops": 8, "cooldown_minutes": 15})
    state = world.state.village
    comm = state.communication
    notice = NoticeVersion(id="notice-1", version=1, author="elsie",
                           terms=NoticeTerms(type="work_request", subject="fish", description="Need fish for supper",
                                             recipients=["rowan"]), created_at=state.minute, updated_at=state.minute)
    comm.notices[notice.id] = [notice]
    comm.placements.setdefault(comm.config.board_id, {})[notice.id] = 1
    assert notice.id not in comm.knowledge.get("rowan", {})
    village_extensions.configure(world, "purposeful", world.revision(),
                                 {"day_policy": VillageDayPolicy(anti_idle=True, purposeful_downtime=True).model_dump()})
    choices = village_fallback.candidates(world)
    assert not any(item["task_id"] == "information:board" for item in choices)
    assert notice.id not in comm.knowledge.get("rowan", {})
    world.actor.location_id = "village-square"
    assert any(item["action_id"] == "village_check_messages" for item in village_fallback.candidates(world))
    assert act(world, "village_check_messages", channel="board")["code"] == "NOTICES_READ"
    assert notice.id in comm.knowledge["rowan"]
    village.advance(world, 5)
    assert not any(item["action_id"] == "village_check_messages" for item in village_fallback.candidates(world))


def test_mill_check_in_is_scheduled_and_mara_relay_is_physical(tmp_path):
    world = make_world(tmp_path, minute=3480)
    config, state = village.context(world)
    world.scenario.village.breakdown_minute = state.minute - 15
    state.breakdown = True
    state.repaired = False
    hobb = world.bind(config.carpenter, f"session-{config.carpenter}")
    mara = world.bind(config.miller, f"session-{config.miller}")
    hobb.actor.location_id = config.residents[config.carpenter].work
    mara.actor.location_id = config.mill
    village.event(mara, "environment", "breakdown", config.mill_equipment, "Mill drive failed", config.mill)
    village_extensions.configure(world, "purposeful", world.revision(),
                                 {"day_policy": VillageDayPolicy(anti_idle=True, purposeful_downtime=True).model_dump()})
    check = next(item for item in village_fallback.candidates(hobb) if item["task_id"] == "incident:mill-check-in")
    assert check["destination"] == config.mill and check["action_id"] == "go"
    relay = next(item for item in village_fallback.candidates(mara) if item["task_id"] == "incident:relay")
    assert relay["destination"] == config.residents[config.carpenter].work
    assert not any("mill drive fail" in message["text"] for message in hobb.actor.messages)
    mara.actor.location_id = hobb.actor.location_id
    say = next(item for item in village_fallback.candidates(mara) if item["task_id"] == "incident:relay")
    assert say["action_id"] == "say"
    assert act(mara, "say", **say["arguments"])["code"] == "SPOKEN"
    assert any("mill drive fail" in message["text"] for message in hobb.actor.messages)
    assert not any(item["action_id"] == "say" and item["task_id"] == "incident:relay"
                   for item in village_fallback.candidates(mara))


def test_runtime_configuration_enables_saved_world_without_reset_or_free_goods(tmp_path):
    world = make_world(tmp_path, enabled=False)
    before = copy.deepcopy(world.state.village.stocks)
    patch = VillageDayPolicy(anti_idle=True, event_driven_waits=True)
    village_extensions.configure(world, "enable-fallback", world.revision(), {"day_policy": patch.model_dump()})
    assert "fallback" in world.observe()["village"]
    assert world.state.village.stocks == before
    world.save()
    restored = World(world.scenario, world.path, "fallback", "rowan", "session-rowan")
    assert village_fallback.enabled(restored)
