import copy
from pathlib import Path

import pytest
from pydantic import ValidationError

from npc_sim import (
    village, village_ai, village_communication as comm, village_economy as economy,
    village_extensions, village_fallback, village_firewood as fuel, village_goods as goods,
    village_livestock, village_mining, village_research, village_walking,
)
from npc_sim.living_village import build_scenario
from npc_sim.models import Scenario, VillageEconomyConfig, VillageFirewoodConfig
from npc_sim.storage import read_json
from npc_sim.world import World
from test_village_expanded import scenario
from test_world import act


def test_living_scenario_has_physical_shared_hearths_and_enabled_fuel():
    from npc_sim.living_village import build_scenario

    base = Scenario.model_validate(read_json(Path(__file__).resolve().parents[1] / "data/scenarios/millhaven-expanded.json"))
    generated = build_scenario(base, firewood=True)
    configured = Scenario.model_validate(read_json(Path(__file__).resolve().parents[1] / "data/scenarios/millhaven-living.json"))
    for value in (generated, configured):
        policy = value.village.firewood
        assert policy is not None and policy.enabled
        assert len(policy.households) == 3
        assert policy.site_capacities["cottages"] == 24
        assert policy.site_reorder_points == {"tavern": 9, "bakery": 7, "forge": 7, "cottages": 18}
        assert policy.site_reserve_targets == {"tavern": 12, "bakery": 10, "forge": 10, "cottages": 24}
        assert {site: value.village.stocks[site]["firewood"] for site in policy.site_capacities} == {
            "tavern": 12, "bakery": 10, "forge": 10, "cottages": 24, "woods": 0,
        }
        assert all(f"cottage-{hearth}" in value.entities for hearth in policy.households)


def make_world(tmp_path, *, minute=600, enabled=True, communication=False, livestock=False):
    value = scenario()
    value.village.start_minute = minute
    value.village.breakdown_minute = max(value.village.breakdown_minute, minute + 240)
    value.village.goal_deadlines = {}
    policy = economy.example_config(value)
    policy.update(reserve_targets={"woods": {"logs": 4}}, stock_buffer={},
                  production_targets={"forge": {"wagon-parts": 1}, "bakery": {"bread": 4}})
    value.village.economy = VillageEconomyConfig.model_validate(policy)
    village_mining.install_scenario(value)
    if livestock:
        village_livestock.install_scenario(value)
    if communication:
        comm.install_scenario(value)
    if enabled:
        fuel.install_scenario(value)
    value = Scenario.model_validate(value.model_dump(mode="json"))
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
    world = World(value, tmp_path / "firewood.json", "firewood", "rowan", "session-rowan")
    for agent in value.agents:
        world.bind(agent.id, f"session-{agent.id}")
    for stock in world.state.village.stocks.values():
        for product in stock:
            stock[product] = 0
    world.state.village.stock_lots = {}
    goods.put(world, "woods", "logs", 4)
    return world


def actor(world, rid, location=None):
    result = world.bind(rid, f"session-{rid}")
    if location:
        result.actor.location_id = location
    return result


def burn_count(world, location):
    return world.state.village.firewood.consumed.get(location, 0)


def test_splitting_uses_actual_logs_axe_time_caps_and_protected_reserve(tmp_path):
    world = make_world(tmp_path)
    assert act(world, fuel.SPLIT)["code"] == "RAW_MATERIAL_RESERVE"
    assert economy.production_targets(world, "woods")["logs"] == 6
    assert act(world, "village_fell_logs")["code"] == "WORK_COMPLETED"
    assert world.state.village.stocks["woods"]["logs"] == 6
    village.advance(world, world.state.village.residents["rowan"].next_work - world.state.village.minute)
    assert act(world, fuel.SPLIT)["code"] == "BATCH_STARTED"
    state = world.state.village
    assert state.stocks["woods"]["logs"] == 5
    assert state.stocks["woods"].get("firewood", 0) == 0
    assert state.residents["rowan"].available_at == state.minute + 15
    door = next(eid for eid, entity in world.state.entities.items()
                if entity.kind == "door" and "woods" in {entity.location, entity.destination})
    assert act(world, "go", target=door)["code"] == "ACTOR_BUSY"
    village.advance(world, 10)
    assert state.stocks["woods"].get("firewood", 0) == 0
    village.advance(world, 5)
    assert state.stocks["woods"]["firewood"] == 4
    assert act(world, fuel.SPLIT)["code"] == "DEMAND_MET"
    assert act(world, "village_fell_logs")["code"] == "DEMAND_MET"
    assert state.stocks["woods"]["logs"] == 5
    assert sum(m.quantity for m in state.economy.movements
               if m.location == "woods" and m.product == "firewood" and m.kind == "produced") == 4


@pytest.mark.parametrize("failure", ["away", "missing", "hidden", "broken", "grip", "skill"])
def test_splitting_requires_physical_location_tool_and_actual_body(tmp_path, failure):
    world = make_world(tmp_path)
    goods.put(world, "woods", "logs", 4)
    expected = {"away": "WRONG_WORKPLACE", "missing": "FIREWOOD_AXE_REQUIRED",
                "hidden": "FIREWOOD_AXE_REQUIRED", "broken": "EQUIPMENT_BROKEN",
                "grip": "BODY_HANDLING_REQUIRED", "skill": "CAPABILITY_REQUIRED"}[failure]
    if failure == "away":
        world.actor.location_id = "bridge"
    elif failure == "missing":
        world.state.entities[fuel.AXE].location = "workshop"
    elif failure == "hidden":
        world.state.entities[fuel.AXE].hidden = True
    elif failure == "broken":
        world.state.entities[fuel.AXE].health = 0
    elif failure == "grip":
        world.actor.body.grips = ["paw"]
    else:
        world.actor.body.capabilities.remove("forestry")
    assert act(world, fuel.SPLIT)["code"] == expected
    assert world.state.village.stocks["woods"]["logs"] == 8
    assert world.state.village.stocks["woods"].get("firewood", 0) == 0


def test_split_keeps_meal_travel_time_and_reserves_the_real_axe(tmp_path):
    world = make_world(tmp_path, minute=1120)
    goods.put(world, "woods", "logs", 4)
    assert act(world, fuel.SPLIT)["code"] == "COMMITMENT_DUE"
    world = make_world(tmp_path / "morning")
    goods.put(world, "woods", "logs", 4)
    assert act(world, fuel.SPLIT)["code"] == "BATCH_STARTED"
    visitor = actor(world, "hobb", "woods")
    result = act(visitor, "take", target=fuel.AXE)
    assert result["outcome"] == "REJECTED"
    assert "reserved" in result["explanation"]
    world.state.entities[fuel.AXE].health = 0
    village.advance(world, 15)
    assert world.state.village.stocks["woods"].get("firewood", 0) == 0
    assert any(event.action == "batch_failed" for event in world.state.village.events)


@pytest.mark.parametrize("rid,action,inputs,output", [
    ("jory", "village_work", {"flour": 2}, "bread"),
    ("elsie", "village_work", {"bread": 2}, "meals"),
    ("elsie", "village_cook_fish", {"fish": 2}, "meals"),
    ("torvin", "village_work", {"iron": 1}, "wagon-parts"),
])
def test_each_real_hot_batch_requires_and_consumes_one_fuel_not_per_portion(tmp_path, rid, action, inputs, output):
    world = make_world(tmp_path, minute=1080)
    worker = actor(world, rid)
    location = worker.actor.location_id
    for product, amount in inputs.items():
        goods.put(world, location, product, amount)
    assert act(worker, action)["code"] == "FIREWOOD_REQUIRED"
    assert world.state.village.stocks[location].get(output, 0) == 0
    for product, amount in inputs.items():
        assert world.state.village.stocks[location][product] == amount
    goods.put(world, location, "firewood", 1)
    request = {"request_id": "one-hot-batch", "expected_observation_revision": worker.revision(),
               "action_id": action, "arguments": {}}
    result = worker.act(**request)
    assert result["outcome"] == "APPLIED", result
    assert world.state.village.stocks[location]["firewood"] == 0
    assert burn_count(world, location) == 1
    assert worker.act(**request) == result
    assert burn_count(world, location) == 1
    village.advance(world, 30)
    assert world.state.village.stocks[location][output] >= 1


def test_rejected_missing_ingredient_does_not_burn_fuel(tmp_path):
    world = make_world(tmp_path, minute=1080)
    baker = actor(world, "jory")
    goods.put(world, "bakery", "firewood", 1)
    assert act(baker, "village_work")["code"] == "STOCK_SHORTAGE"
    assert burn_count(world, "bakery") == 0
    assert world.state.village.stocks["bakery"]["firewood"] == 1


def test_dairy_cooking_also_needs_real_fuel(tmp_path):
    world = make_world(tmp_path, minute=1080, livestock=True)
    cook = actor(world, "elsie")
    goods.put(world, "tavern", "milk", 1)
    goods.put(world, "tavern", "eggs", 1)
    assert act(cook, "village_cook_dairy")["code"] == "FIREWOOD_REQUIRED"
    goods.put(world, "tavern", "firewood", 1)
    assert act(cook, "village_cook_dairy")["code"] == "LIVESTOCK_STARTED"
    assert burn_count(world, "tavern") == 1
    village.advance(world, 15)
    assert world.state.village.stocks["tavern"]["meals"] == 2
    assert world.state.village.stocks["tavern"]["firewood"] == 0


def test_smelt_uses_firewood_instead_of_double_charging_logs(tmp_path):
    world = make_world(tmp_path)
    smith = actor(world, "torvin")
    goods.put(world, "forge", "iron-ore", 1)
    goods.put(world, "forge", "logs", 3)
    assert act(smith, "village_smelt_iron")["code"] == "FIREWOOD_REQUIRED"
    goods.put(world, "forge", "firewood", 1)
    assert act(smith, "village_smelt_iron")["code"] == "SMELTING_STARTED"
    assert world.state.village.stocks["forge"]["logs"] == 3
    assert world.state.village.stocks["forge"]["iron-ore"] == 0
    assert world.state.village.stocks["forge"]["firewood"] == 0
    assert not village_mining.fuel_requests(smith, "torvin")
    village.advance(world, 30)
    assert world.state.village.stocks["forge"]["iron"] == 1
    assert burn_count(world, "forge") == 1


def test_only_occupied_shared_hearths_burn_once_not_per_resident(tmp_path):
    world = make_world(tmp_path, minute=1080)
    config = world.state.village.firewood.config
    # Iona already works at cottages; move the whole first group home too.
    for rid in config.households["hearth-1"]:
        actor(world, rid, "cottages")
    goods.put(world, "cottages", "firewood", 4)
    village.advance(world, 5)
    assert burn_count(world, "cottages") == 2
    assert world.state.village.stocks["cottages"]["firewood"] == 2
    fuel.advance(world)
    assert burn_count(world, "cottages") == 2
    village.advance(world, 235)
    assert burn_count(world, "cottages") == 2
    village.advance(world, 5)
    assert burn_count(world, "cottages") == 4
    report = fuel.monitor(world)["firewood"]
    assert [row["status"] for row in report["households"]] == ["warm", "warm", "unoccupied"]


@pytest.mark.parametrize("season,burn_minutes", [("spring", 240), ("autumn", 240), ("winter", 120), ("summer", 0)])
def test_seasonal_heating_rates_and_summer_no_household_burn(tmp_path, season, burn_minutes):
    world = make_world(tmp_path, minute=1080)
    state = world.state.village
    state.firewood.config.season = season
    goods.put(world, "cottages", "firewood", 3)
    village.advance(world, 5)
    assert burn_count(world, "cottages") == (1 if burn_minutes else 0)
    if burn_minutes:
        assert state.firewood.warm_until["hearth-2"] == state.minute + burn_minutes
        village.advance(world, burn_minutes)
        assert burn_count(world, "cottages") == 2
    else:
        assert all(row["status"] == "not_needed" for row in fuel.monitor(world)["firewood"]["households"])
        assert fuel.consumer_target(world, "iona") == 0
        assert not fuel.requests(actor(world, "iona"), "iona")


def test_daytime_and_unoccupied_homes_do_not_burn_and_cold_has_no_health_penalty(tmp_path):
    world = make_world(tmp_path)
    goods.put(world, "cottages", "firewood", 2)
    village.advance(world, 10)
    assert burn_count(world, "cottages") == 0
    world = make_world(tmp_path / "evening", minute=1080)
    actor(world, "iona", "village-square")
    village.advance(world, 5)
    assert burn_count(world, "cottages") == 0
    actor(world, "hobb", "cottages")
    before = copy.deepcopy(world.state.actors["hobb"].needs)
    village.advance(world, 10)
    assert world.state.actors["hobb"].needs == before
    assert next(row for row in fuel.monitor(world)["firewood"]["households"]
                if "hobb" in row["members"])["status"] == "cold"
    events = [event for event in world.state.village.events if event.action == "household_cold"]
    assert len(events) == 1
    assert "iona" not in events[0].witnesses
    assert "hobb" in events[0].witnesses
    assert act(actor(world, "hobb"), "village_observe")["code"] == "LOCAL_TOOLS_CHECKED"


def test_overnight_window_ends_at_seven_without_backdated_fuel_or_new_penalties(tmp_path):
    world = make_world(tmp_path, minute=400)
    for rid in world.scenario.village.residents:
        actor(world, rid, "cottages")
    goods.put(world, "cottages", "firewood", 3)
    village.advance(world, 5)
    assert set(world.state.village.firewood.warm_until.values()) == {420}
    village.advance(world, 30)
    assert burn_count(world, "cottages") == 3
    assert all(row["status"] == "not_needed" for row in fuel.monitor(world)["firewood"]["households"])


def test_actual_fuel_requests_use_local_stock_affordable_funds_and_no_remote_stock_knowledge(tmp_path):
    world = make_world(tmp_path)
    keeper = actor(world, "iona")
    request = next(row for row in economy.suggestions(keeper) if row["product"] == "firewood")
    assert request["supplier"] == "rowan" and request["destination"] == "cottages"
    assert request["quantity"] == 10  # Real 20-coin purse, including conservative fee reservations.
    assert request["commitment"] == 20
    keeper.actor.location_id = "village-square"
    assert not fuel.requests(keeper, "iona")
    assert not fuel.observation(keeper)["firewood"]["sites"]
    assert not fuel.observation(keeper)["firewood"]["households"]
    keeper.actor.location_id = "cottages"
    world.state.village.coins["iona"] = 0
    assert not [row for row in economy.suggestions(keeper) if row["product"] == "firewood"]


def test_site_fuel_thresholds_and_targets_are_validated_and_trigger_locally(tmp_path):
    world = make_world(tmp_path)
    config = world.state.village.firewood.config
    config.site_reorder_points = {"tavern": 9, "bakery": 7, "forge": 7, "cottages": 12}
    config.site_reserve_targets = {"tavern": 12, "bakery": 10, "forge": 10, "cottages": 16}
    for resident, location, threshold, target in (
        ("elsie", "tavern", 9, 12), ("jory", "bakery", 7, 10),
        ("torvin", "forge", 7, 10), ("iona", "cottages", 12, 16),
    ):
        buyer = actor(world, resident, location)
        goods.put(world, location, "firewood", threshold + 1)
        assert not fuel.requests(buyer, resident)
        goods.take(world, location, "firewood", 1)
        assert fuel.requests(buyer, resident)[0][2] == {"firewood": target}
    original = config.model_dump()
    for updates in ({"site_reserve_targets": {"tavern": 17}},
                    {"site_reorder_points": {"not-a-site": 1}},
                    {"site_reorder_points": {"tavern": 13}},
                    {"site_reorder_points": {"tavern": -1}}):
        value = original | updates
        with pytest.raises(ValueError, match="Fuel reorder points and reserve targets"):
            fuel._validate(world.scenario, VillageFirewoodConfig.model_validate(value))


def test_funded_order_requires_actual_haul_unloading_and_payment_then_recurs(tmp_path):
    world = make_world(tmp_path)
    buyer = actor(world, "iona", "woods")
    result = economy.apply(buyer, "village_propose_order", {
        "product": "firewood", "quantity": 2, "supplier": "rowan",
        "deadline": 1000, "required_fresh_until": 1080})
    assert result[:2] == ("APPLIED", "ORDER_PROPOSED")
    order = next(iter(world.state.village.economy.orders.values()))
    assert economy.apply(world, "village_accept_order", {"order_id": order.id})[:2] == ("APPLIED", "ORDER_ACCEPTED_PLANNED")
    assert order.status == "accepted_planned"
    goods.put(world, "woods", "logs", 4)
    assert act(world, fuel.SPLIT)["code"] == "BATCH_STARTED"
    village.advance(world, 15)
    stocks = world.state.village.stocks
    balances = world.state.village.coins.copy()
    assert act(world, "village_load_goods", goods={"firewood": 2}, destination="cottages")["outcome"] == "APPLIED"
    assert stocks["cottages"].get("firewood", 0) == 0
    assert world.state.village.coins == balances
    village.advance(world, 5)
    assert act(world, "village_unload_goods")["outcome"] == "REJECTED"
    for destination in village.route(world, world.actor.location_id, "cottages"):
        door = next(eid for eid, entity in world.state.entities.items()
                    if entity.kind == "door" and {entity.location, entity.destination}
                    == {world.actor.location_id, destination})
        assert act(world, "go", target=door)["outcome"] == "APPLIED"
        village.advance(world, 5)
    assert act(world, "village_unload_goods")["outcome"] == "APPLIED"
    assert stocks["cottages"]["firewood"] == 2
    assert world.state.village.coins["iona"] == balances["iona"] - 3
    assert world.state.village.coins["rowan"] == balances["rowan"] + 3
    assert order.status == "fulfilled"
    buyer.actor.location_id = "cottages"
    before = next(row for row in economy.suggestions(buyer) if row["product"] == "firewood")
    world.state.village.minute = 1080
    fuel.advance(world)
    after = next(row for row in economy.suggestions(buyer) if row["product"] == "firewood")
    assert after["request_key"] != before["request_key"]
    assert stocks["cottages"]["firewood"] == 0  # Iona's and Rowan's occupied groups each burn once.


def test_fuel_acceptance_uses_existing_uncommitted_stock_before_production(tmp_path):
    world = make_world(tmp_path)
    goods.put(world, "woods", "firewood", 3)
    world.state.village.economy.config.reserve_targets["woods"] = {}
    buyer = actor(world, "jory", "woods")
    assert economy.apply(buyer, "village_propose_order", {
        "product": "firewood", "quantity": 2, "supplier": "rowan",
        "deadline": 700, "required_fresh_until": 760})[0] == "APPLIED"
    order = next(iter(world.state.village.economy.orders.values()))
    world.state.entities[fuel.AXE].hidden = True
    result = economy.apply(world, "village_accept_order", {"order_id": order.id})
    assert result[:2] == ("APPLIED", "ORDER_ACCEPTED")
    assert order.production_plan == {} and order.loadable == 2
    assert world.state.village.stocks["woods"]["firewood"] == 3
    assert economy.apply(world, "village_accept_order", {"order_id": order.id})[1] == "ORDER_ALREADY_ACCEPTED"
    assert act(world, "village_load_goods", goods={"firewood": 2}, destination="bakery")["outcome"] == "APPLIED"
    assert world.state.village.stocks["woods"]["firewood"] == 1
    assert order.in_transit == 2


def test_physical_notices_keep_supplier_private_and_allow_real_future_felling_capacity(tmp_path):
    world = make_world(tmp_path, communication=True)
    keeper = actor(world, "iona", "village-square")
    world.state.village.coins["iona"] = 100
    terms = {"type": "supply_request", "subject": "firewood", "description": "Fuel for shared cottage hearths",
             "recipients": ["rowan"], "quantity": 14, "destination": "cottages",
             "due_at": 1080, "required_fresh_until": 1140}
    assert comm.apply(keeper, "village_notice_post", {"channel": "board", "notice": terms})[0] == "APPLIED"
    nid = next(iter(world.state.village.communication.notices))
    assert nid not in world.state.village.communication.knowledge.get("rowan", {})
    assert fuel.production_potential(world, 900) >= 14
    world.actor.location_id = "village-square"
    assert comm.apply(world, "village_notice_read", {"notice_ids": [nid]})[0] == "APPLIED"
    result = comm.apply(world, "village_notice_reply", {
        "channel": "board", "notice_id": nid, "expected_version": 1, "response": "accept"})
    assert result[0] == "APPLIED", result
    order = next(iter(world.state.village.economy.orders.values()))
    assert order.status == "accepted_planned" and order.destination == "cottages"
    assert world.state.village.stocks["woods"]["logs"] == 4
    assert world.state.village.stocks["woods"].get("firewood", 0) == 0
    world.actor.location_id = "woods"
    assert economy.production_targets(world, "woods", personal=True)["logs"] >= 10
    assert act(world, "village_fell_logs")["outcome"] == "APPLIED"


def test_opt_out_and_runtime_install_preserve_assets_and_persist_without_mutating_scenario(tmp_path):
    world = make_world(tmp_path, enabled=False)
    assert fuel.SPLIT not in {row["id"] for row in world.actions()}
    smith = actor(world, "torvin")
    goods.put(world, "forge", "iron-ore", 1)
    goods.put(world, "forge", "logs", 1)
    assert act(smith, "village_smelt_iron")["code"] == "SMELTING_STARTED"
    assert world.state.village.stocks["forge"]["logs"] == 0
    before_coins = world.state.village.coins.copy()
    frozen = world.scenario.model_dump(mode="json")
    values = fuel.example_config(world.scenario)
    revision = world.revision()
    response = village_extensions.configure(world, "fuel-install", revision, {"firewood": values})
    assert response["configured"] == ["firewood"]
    assert village_extensions.configure(world, "fuel-install", revision, {"firewood": values}) == response
    assert world.scenario.model_dump(mode="json") == frozen
    assert world.state.village.coins == before_coins
    assert all(stock["firewood"] == 0 for stock in world.state.village.stocks.values())
    assert village_research.prices(world)["firewood"] == 1
    with pytest.raises(ValueError, match="fresh revision"):
        village_extensions.configure(world, "stale", revision, {"firewood": values})
    village.advance(world, 30)
    actor(world, "iona", "cottages")
    world.state.village.minute = 1080
    goods.put(world, "cottages", "firewood", 1)
    fuel.advance(world)
    world.save()
    restored = World(world.scenario, world.path, "firewood", "rowan", "session-rowan")
    assert restored.state.village.firewood == world.state.village.firewood
    assert restored.state.village.dashboard.firewood == world.state.village.dashboard.firewood
    assert restored.scenario.village.firewood is None


def test_strict_config_atomic_install_and_complete_household_membership(tmp_path):
    world = make_world(tmp_path, enabled=False)
    values = fuel.example_config(world.scenario)
    with pytest.raises(ValidationError):
        VillageFirewoodConfig.model_validate({**values, "firewood_per_log": True})
    bad = copy.deepcopy(values)
    bad["households"]["hearth-1"].append("iona")
    before = world.state.model_dump(mode="json")
    with pytest.raises(ValueError, match="exactly once"):
        village_extensions.configure(world, "bad-fuel", world.revision(), {"firewood": bad})
    assert world.state.model_dump(mode="json") == before
    bad = {**values, "businesses": ["jory"]}
    with pytest.raises(ValueError, match="smith, innkeeper"):
        village_extensions.configure(world, "missing-forge", world.revision(), {"firewood": bad})
    assert world.state.model_dump(mode="json") == before


@pytest.mark.parametrize("recipe_fuel", [False, True])
def test_heated_research_trial_and_verified_recipe_burn_only_real_fuel_once(tmp_path, monkeypatch, recipe_fuel):
    world = make_world(tmp_path)
    world.state.village.economy.config.production_targets = {}
    village_research.configure(world, {"required_minutes": 60, "required_successes": 1})
    worker = actor(world, "torvin" if recipe_fuel else "jory")
    location = worker.actor.location_id
    ingredients = {"logs": 1, "firewood": 1} if recipe_fuel else {"flour": 1}
    for product, amount in ingredients.items():
        if product != "firewood":
            goods.put(world, location, product, amount * 2)
    result = act(worker, "village_research_propose", title="New heated material" if recipe_fuel else "Herb loaf",
                 description="An occupational recipe with real heated ingredients.",
                 skill=world.scenario.village.residents[worker.actor_id].skills[0],
                 kind="material" if recipe_fuel else "food", inputs=ingredients,
                 equipment=["anvil" if recipe_fuel else "oven"], duration=30)
    assert result["outcome"] == "APPLIED", result
    village.advance(world, 5)
    key = next(iter(world.state.village.research.projects))
    assert act(worker, "village_research", project=key)["code"] == "FIREWOOD_REQUIRED"
    goods.put(world, location, "firewood", 1)
    monkeypatch.setattr(village_research, "_trial_success", lambda *_: True)
    for _ in range(4):
        result = act(worker, "village_research", project=key)
        assert result["code"] == "RESEARCH_STARTED", result
        village.advance(world, 15)
    assert burn_count(world, location) == 1
    assert world.state.village.research.projects[key].discovered_at is not None
    assert act(worker, "village_research_make", project=key)["code"] == "FIREWOOD_REQUIRED"
    goods.put(world, location, "firewood", 1)
    assert act(worker, "village_research_make", project=key)["code"] == "DISCOVERY_WORK_STARTED"
    assert burn_count(world, location) == 2
    assert world.state.village.stocks[location]["firewood"] == 0
    village.advance(world, 30)
    assert world.state.village.stocks[location][key] == 1


def test_firewood_does_not_spend_logs_promised_for_actual_construction_timber(tmp_path):
    world = make_world(tmp_path)
    carpenter = actor(world, "hobb", "woods")
    assert economy.apply(carpenter, "village_propose_order", {
        "product": "timber", "quantity": 1, "supplier": "rowan",
        "deadline": 1000, "required_fresh_until": 1080})[0] == "APPLIED"
    order = next(iter(world.state.village.economy.orders.values()))
    goods.put(world, "woods", "logs", 1)
    assert economy.apply(world, "village_accept_order", {"order_id": order.id})[0] == "APPLIED"
    assert order.status == "accepted_planned"
    assert act(world, fuel.SPLIT)["code"] == "RAW_MATERIAL_RESERVE"
    assert act(world, "village_work")["code"] == "WORK_COMPLETED"
    assert world.state.village.stocks["woods"]["logs"] == 4
    assert world.state.village.stocks["woods"]["timber"] == 1


def test_heating_needs_explicit_enable_even_when_fuel_feature_is_installed(tmp_path):
    world = make_world(tmp_path, minute=1080)
    world.state.village.firewood.config.heating_enabled = False
    goods.put(world, "cottages", "firewood", 3)
    village.advance(world, 10)
    assert world.state.village.stocks["cottages"]["firewood"] == 3
    assert fuel.consumer_target(world, "iona") == 0
    assert all(hearth["status"] == "not_needed" for hearth in fuel.monitor(world)["firewood"]["households"])


def test_fuel_capacities_block_overproduction_and_atomic_unloading(tmp_path):
    world = make_world(tmp_path)
    config = world.state.village.firewood.config
    assert config.site_capacities["woods"] == 24
    assert config.site_capacities["cottages"] == 16
    goods.put(world, "woods", "firewood", 21)
    goods.put(world, "woods", "logs", 4)
    world.state.village.economy.config.production_targets["woods"] = {"firewood": 24}
    assert fuel.action_blocker(world, fuel.SPLIT)[0] == "FIREWOOD_STORAGE_FULL"
    assert act(world, fuel.SPLIT)["code"] in {"DEMAND_MET", "FIREWOOD_STORAGE_FULL"}
    assert world.state.village.stocks["woods"]["logs"] == 8
    with pytest.raises(ValueError, match="FIREWOOD_STORAGE_FULL"):
        goods.put(world, "woods", "firewood", 4)
    assert world.state.village.stocks["woods"]["firewood"] == 21

    goods.put(world, "cottages", "firewood", 15)
    buyer = actor(world, "iona", "woods")
    assert economy.apply(buyer, "village_propose_order", {
        "product": "firewood", "quantity": 2, "supplier": "rowan",
        "deadline": 1000, "required_fresh_until": 1080})[0] == "APPLIED"
    order = next(iter(world.state.village.economy.orders.values()))
    assert economy.apply(world, "village_accept_order", {"order_id": order.id})[0] == "APPLIED"
    assert act(world, "village_load_goods", goods={"firewood": 2}, destination="cottages")["outcome"] == "REJECTED"
    assert world.state.village.stocks["woods"]["firewood"] == 21
    goods.take(world, "cottages", "firewood", 1)
    assert act(world, "village_load_goods", goods={"firewood": 2}, destination="cottages")["outcome"] == "APPLIED"
    village.advance(world, 5)
    goods.put(world, "cottages", "firewood", 2)
    for destination in village.route(world, world.actor.location_id, "cottages"):
        door = next(eid for eid, entity in world.state.entities.items()
                    if entity.kind == "door" and {entity.location, entity.destination}
                    == {world.actor.location_id, destination})
        assert act(world, "go", target=door)["outcome"] == "APPLIED"
        village.advance(world, 5)
    balances = world.state.village.coins.copy()
    assert act(world, "village_unload_goods")["outcome"] == "REJECTED"
    assert world.state.village.coins == balances
    assert world.state.village.stocks["cottages"]["firewood"] == 16
    assert order.in_transit == 2


def test_fuel_delivery_plan_groups_only_confirmed_feasible_stops(tmp_path):
    world = make_world(tmp_path)
    goods.put(world, "woods", "firewood", 12)
    assert fuel.delivery_plan(world) is None
    for buyer_id, destination in (("iona", "cottages"), ("jory", "bakery")):
        buyer = actor(world, buyer_id, "woods")
        result = economy.apply(buyer, "village_propose_order", {
            "product": "firewood", "quantity": 1, "supplier": "rowan",
            "deadline": 1000, "required_fresh_until": 1080})
        assert result[0] == "APPLIED", result
        order = next(order for order in world.state.village.economy.orders.values()
                     if order.buyer == buyer_id)
        assert economy.apply(world, "village_accept_order", {"order_id": order.id})[0] == "APPLIED"
    plan = fuel.delivery_plan(world)
    assert plan is not None
    assert plan.load_plan == {"firewood": 2} and plan.vehicle_id is None
    assert len(plan.stops) == 2 and {stop.destination for stop in plan.stops} == {"cottages", "bakery"}
    assert sum(stop.quantity for stop in plan.stops) == plan.remaining_inventory
    current = "woods"
    for stop in plan.stops:
        assert stop.path == village.route(world, current, stop.destination)
        current = stop.destination
    grouped = sum(len(stop.path) for stop in plan.stops)
    separate = sum(len(village.route(world, "woods", stop.destination)) for stop in plan.stops)
    assert grouped <= separate
    assert world.state.village.stocks["woods"]["firewood"] == 12
    assert not world.state.village.firewood.delivery_plans
    stored = fuel.save_delivery_plan(world)
    assert stored == plan and stored.status == "planned"
    assert world.state.village.stocks["woods"]["firewood"] == 12
    assert fuel.save_delivery_plan(world) == plan
    assert len(world.state.village.firewood.delivery_plans) == 1
    world.state.entities[next(eid for eid, entity in world.state.entities.items()
                              if entity.kind == "door" and {entity.location, entity.destination}
                              == {"woods", plan.stops[0].path[0]})].open = False
    blocked = fuel.delivery_plan(world)
    assert blocked is None or all(stop.path == village.route(world, "woods" if index == 0
                             else blocked.stops[index - 1].destination, stop.destination)
                             for index, stop in enumerate(blocked.stops))


def test_blocked_fuel_commitment_uses_newly_split_stock_for_real_route(tmp_path):
    world = make_world(tmp_path)
    goods.put(world, "woods", "firewood", 4)
    buyer = actor(world, "jory", "woods")
    assert economy.apply(buyer, "village_propose_order", {
        "product": "firewood", "quantity": 1, "supplier": "rowan",
        "deadline": 1000, "required_fresh_until": 1080})[0] == "APPLIED"
    order = next(iter(world.state.village.economy.orders.values()))
    assert economy.apply(world, "village_accept_order", {"order_id": order.id})[0] == "APPLIED"
    order.status = "blocked"
    assert fuel.delivery_plan(world) is not None
    assert fuel.delivery_plan(world).stops[0].order_id == order.id
    assert world.state.village.stocks["woods"]["firewood"] == 4


def _fund_grouped_route(world):
    goods.put(world, "woods", "firewood", 12)
    for buyer_id in ("iona", "jory"):
        buyer = actor(world, buyer_id, "woods")
        world.state.village.coins[buyer_id] = 50
        result = economy.apply(buyer, "village_propose_order", {
            "product": "firewood", "quantity": 1, "supplier": "rowan",
            "deadline": 1000, "required_fresh_until": 1080})
        assert result[0] == "APPLIED", result
        order = next(item for item in world.state.village.economy.orders.values() if item.buyer == buyer_id)
        assert economy.apply(world, "village_accept_order", {"order_id": order.id})[0] == "APPLIED"
    return fuel.save_delivery_plan(world)


def test_advertised_fuel_route_executes_one_physical_step_per_request(tmp_path, monkeypatch):
    world = make_world(tmp_path)
    plan = _fund_grouped_route(world)
    assert plan is not None
    action_id = "village_execute_fuel_delivery"
    descriptor = next(item for item in world.actions() if item["id"] == action_id)
    assert descriptor["arguments_schema"]["properties"]["route_id"]["enum"] == [plan.route_id]
    assert act(world, action_id, route_id="missing")["code"] == "INVALID_ARGUMENTS"
    original_act = world.act
    calls = []
    def record(*args, **kwargs):
        calls.append(args[2] if len(args) > 2 else kwargs["action_id"])
        return original_act(*args, **kwargs)
    monkeypatch.setattr(world, "act", record)
    initial = world.state.village.coins.copy()
    steps = []
    for _ in range(32):
        if plan.status == "completed":
            break
        result = act(world, action_id, route_id=plan.route_id)
        assert result["outcome"] == "APPLIED", result
        steps.append(result["code"])
        village.advance(world, 5)
    assert plan.status == "completed"
    assert set(steps) == {"FUEL_ROUTE_LOADED", "FUEL_ROUTE_TRAVEL", "FUEL_ROUTE_DELIVERED"}
    assert len(calls) == len(steps) and set(calls) == {action_id}
    assert world.state.village.stocks["woods"]["firewood"] == 10
    for stop in plan.stops:
        order = world.state.village.economy.orders[stop.order_id]
        assert order.status == "fulfilled"
        assert world.state.village.coins[order.buyer] == initial[order.buyer] - order.price - order.delivery_fee
    assert world.state.village.coins["rowan"] == initial["rowan"] + sum(
        world.state.village.economy.orders[stop.order_id].price +
        world.state.village.economy.orders[stop.order_id].delivery_fee for stop in plan.stops)
    assert action_id not in {item["id"] for item in world.actions()}
    restored = World(world.scenario, world.path, world.state.world_id, "rowan", "session-rowan")
    assert restored.state.village.firewood.delivery_plans[0].status == "completed"


def test_fuel_route_action_advertises_feasible_unsaved_route_without_automatic_effects(tmp_path):
    world = make_world(tmp_path)
    goods.put(world, "woods", "firewood", 12)
    buyer = actor(world, "iona", "woods")
    world.state.village.coins["iona"] = 50
    assert economy.apply(buyer, "village_propose_order", {
        "product": "firewood", "quantity": 1, "supplier": "rowan",
        "deadline": 1000, "required_fresh_until": 1080})[0] == "APPLIED"
    order = next(iter(world.state.village.economy.orders.values()))
    assert economy.apply(world, "village_accept_order", {"order_id": order.id})[0] == "APPLIED"
    action_id = "village_execute_fuel_delivery"
    descriptor = next(item for item in world.actions() if item["id"] == action_id)
    route_id = descriptor["arguments_schema"]["properties"]["route_id"]["enum"][0]
    assert not world.state.village.firewood.delivery_plans
    assert world.state.village.stocks["woods"]["firewood"] == 12
    result = act(world, action_id, route_id=route_id)
    assert result["code"] == "FUEL_ROUTE_LOADED", result
    assert world.state.village.firewood.delivery_plans[0].route_id == route_id
    assert world.state.village.stocks["woods"]["firewood"] == 11


def test_fuel_route_action_replays_request_and_retains_cargo_at_blocked_stop(tmp_path):
    world = make_world(tmp_path)
    plan = _fund_grouped_route(world)
    action_id = "village_execute_fuel_delivery"
    request = "fuel-once"
    revision = world.revision()
    first = world.act(request, revision, action_id, {"route_id": plan.route_id})
    assert first["code"] == "FUEL_ROUTE_LOADED"
    assert world.act(request, revision, action_id, {"route_id": plan.route_id}) == first
    village.advance(world, 5)
    assert act(world, action_id, route_id=plan.route_id)["code"] == "FUEL_ROUTE_LOADED"
    door = next(entity for entity in world.state.entities.values()
                if entity.kind == "door" and {entity.location, entity.destination}
                == {"woods", plan.stops[0].path[0]})
    door.open = False
    village.advance(world, 5)
    assert act(world, action_id, route_id=plan.route_id)["code"] == "FUEL_ROUTE_BLOCKED"
    assert world.actor.location_id == "woods"
    assert sum(item.quantity for item in world.state.village.residents["rowan"].shipments) == 2
    assert plan.status == "loaded"
    door.open = True
    assert act(world, action_id, route_id=plan.route_id)["code"] == "FUEL_ROUTE_TRAVEL"


def test_loaded_fuel_route_reroutes_after_an_edge_closes_without_reloading(tmp_path):
    world = make_world(tmp_path)
    plan = _fund_grouped_route(world)
    action_id = "village_execute_fuel_delivery"
    for _ in plan.stops:
        assert act(world, action_id, route_id=plan.route_id)["code"] == "FUEL_ROUTE_LOADED"
        village.advance(world, 5)
    assert act(world, action_id, route_id=plan.route_id)["code"] == "FUEL_ROUTE_TRAVEL"
    assert world.actor.location_id == "bridge"
    village.advance(world, 5)
    blocked_edge = next(entity for entity in world.state.entities.values()
                        if entity.kind == "door" and {entity.location, entity.destination}
                        == {"bridge", "village-square"})
    blocked_edge.open = False
    assert village.route(world, "bridge", "cottages") == ["watermill", "farm", "village-square", "cottages"]
    stock = world.state.village.stocks["woods"]["firewood"]
    cargo = sum(item.quantity for item in world.state.village.residents["rowan"].shipments)
    assert act(world, action_id, route_id=plan.route_id)["code"] == "FUEL_ROUTE_TRAVEL"
    assert world.actor.location_id == "watermill"
    assert world.state.village.stocks["woods"]["firewood"] == stock
    assert sum(item.quantity for item in world.state.village.residents["rowan"].shipments) == cargo
    blocked_edge.open = True
    for _ in range(32):
        if plan.status == "completed":
            break
        village.advance(world, 5)
        result = act(world, action_id, route_id=plan.route_id)
        assert result["outcome"] == "APPLIED", result
    assert plan.status == "completed"
    assert all(world.state.village.economy.orders[stop.order_id].delivered_quantity == 1 for stop in plan.stops)


def test_loaded_fuel_route_reconciles_conflicting_planned_walk(tmp_path):
    world = make_world(tmp_path)
    plan = _fund_grouped_route(world)
    for _ in plan.stops:
        assert act(world, "village_execute_fuel_delivery", route_id=plan.route_id)["code"] == "FUEL_ROUTE_LOADED"
        village.advance(world, 5)
    world.scenario.id = "millhaven-living"
    world.scenario.village.time_based_turns = True
    person = world.state.village.residents["rowan"]
    person.known_routes = [eid for eid, entity in world.state.entities.items() if entity.kind == "door"]
    assert village_walking.plan(world, {"destination": "bakery", "purpose": "Visit bakery"})[1] == "ROUTE_PLANNED"
    assert person.walking_plan is not None
    result = act(world, "village_execute_fuel_delivery", route_id=plan.route_id)
    assert result["code"] == "FUEL_ROUTE_TRAVEL", result
    for _ in range(32):
        if plan.status == "completed":
            break
        village.advance(world, 5)
        result = act(world, "village_execute_fuel_delivery", route_id=plan.route_id)
        assert result["outcome"] == "APPLIED", result
    assert plan.status == "completed"
    assert person.walking_plan is None
    assert not person.shipments
    assert all(world.state.village.economy.orders[stop.order_id].delivered_quantity == stop.quantity
               for stop in plan.stops)


def test_fuel_delivery_retry_does_not_duplicate_unload_or_payment(tmp_path):
    world = make_world(tmp_path)
    plan = _fund_grouped_route(world)
    for _ in plan.stops:
        assert act(world, "village_execute_fuel_delivery", route_id=plan.route_id)["code"] == "FUEL_ROUTE_LOADED"
        village.advance(world, 5)
    for _ in range(12):
        if world.actor.location_id == plan.stops[0].destination:
            break
        assert act(world, "village_execute_fuel_delivery", route_id=plan.route_id)["code"] == "FUEL_ROUTE_TRAVEL"
        village.advance(world, 5)
    stop = plan.stops[0]
    order = world.state.village.economy.orders[stop.order_id]
    request, revision = "fuel-unload-once", world.revision()
    result = world.act(request, revision, "village_execute_fuel_delivery", {"route_id": plan.route_id})
    assert result["code"] == "FUEL_ROUTE_DELIVERED"
    stock = world.state.village.stocks[stop.destination]["firewood"]
    coins = world.state.village.coins.copy()
    assert world.act(request, revision, "village_execute_fuel_delivery", {"route_id": plan.route_id}) == result
    assert world.state.village.stocks[stop.destination]["firewood"] == stock == 1
    assert world.state.village.coins == coins and order.delivered_quantity == 1


def test_late_fuel_route_retains_loaded_shipment_and_reaches_destination(tmp_path):
    world = make_world(tmp_path)
    plan = _fund_grouped_route(world)
    for _ in plan.stops:
        assert act(world, "village_execute_fuel_delivery", route_id=plan.route_id)["code"] == "FUEL_ROUTE_LOADED"
        village.advance(world, 5)
    order = world.state.village.economy.orders[plan.stops[0].order_id]
    world.state.village.minute = order.deadline + 1
    initial = world.state.village.stocks["woods"]["firewood"]
    for _ in range(12):
        if world.actor.location_id == plan.stops[0].destination:
            break
        result = act(world, "village_execute_fuel_delivery", route_id=plan.route_id)
        assert result["code"] == "FUEL_ROUTE_TRAVEL", result
        village.advance(world, 5)
    assert world.actor.location_id == plan.stops[0].destination
    assert world.state.village.stocks["woods"]["firewood"] == initial
    assert order.in_transit == 1
    assert sum(item.quantity for item in world.state.village.residents["rowan"].shipments) == 2
    coins = world.state.village.coins.copy()
    result = act(world, "village_execute_fuel_delivery", route_id=plan.route_id)
    assert result["outcome"] == "REJECTED", result
    assert world.state.village.coins == coins
    assert order.in_transit == 1
    assert world.state.village.residents["rowan"].shipments


def test_late_fuel_buyer_decision_requires_loaded_cargo_local_buyer_and_is_final(tmp_path):
    world = make_world(tmp_path)
    plan = _fund_grouped_route(world)
    for _ in plan.stops:
        assert act(world, "village_execute_fuel_delivery", route_id=plan.route_id)["code"] == "FUEL_ROUTE_LOADED"
        village.advance(world, 5)
    order = world.state.village.economy.orders[plan.stops[0].order_id]
    buyer = actor(world, order.buyer, order.destination)
    assert fuel.respond_late_delivery(buyer, order.id, True)[1] == "NOT_LATE_DELIVERY"
    world.state.village.minute = order.deadline + 1
    economy.advance(world)
    assert order.status == "failed" and order.in_transit == 1
    assert fuel.respond_late_delivery(world, order.id, True)[1] == "UNKNOWN_FUEL_ORDER"
    buyer.actor.location_id = "woods"
    assert fuel.respond_late_delivery(buyer, order.id, True)[1] == "BUYER_NOT_AT_SITE"
    buyer.actor.location_id = order.destination
    assert fuel.respond_late_delivery(buyer, order.id, False)[1] == "LATE_FUEL_REJECTED"
    assert fuel.respond_late_delivery(buyer, order.id, False)[1] == "LATE_FUEL_DECISION_EXISTS"
    assert fuel.respond_late_delivery(buyer, order.id, True)[1] == "DECISION_FINAL"
    assert world.state.village.firewood.statuses[f"late:{order.id}"] == "rejected"
    assert order.in_transit == 1 and order.delivered_quantity == 0


def test_rejected_late_fuel_keeps_unpaid_cargo_for_physical_return(tmp_path):
    world = make_world(tmp_path)
    plan = _fund_grouped_route(world)
    for _ in plan.stops:
        assert act(world, "village_execute_fuel_delivery", route_id=plan.route_id)["code"] == "FUEL_ROUTE_LOADED"
        village.advance(world, 5)
    stop = plan.stops[0]
    order = world.state.village.economy.orders[stop.order_id]
    world.state.village.minute = order.deadline + 1
    economy.advance(world)
    buyer = actor(world, order.buyer, stop.destination)
    assert fuel.respond_late_delivery(buyer, order.id, False)[1] == "LATE_FUEL_REJECTED"
    for _ in range(12):
        if world.actor.location_id == stop.destination:
            break
        result = act(world, "village_execute_fuel_delivery", route_id=plan.route_id)
        assert result["code"] == "FUEL_ROUTE_TRAVEL", result
        village.advance(world, 5)
    assert world.actor.location_id == stop.destination
    coins = world.state.village.coins.copy()
    assert act(world, "village_execute_fuel_delivery", route_id=plan.route_id)["code"] == "ORDER_CLOSED"
    assert order.in_transit == 1 and order.delivered_quantity == 0
    assert world.state.village.coins == coins
    assert world.state.village.stocks[stop.destination]["firewood"] == 0
    path = village.route(world, stop.destination, order.source)
    for destination in path:
        door = next(eid for eid, entity in world.state.entities.items()
                    if entity.kind == "door" and {entity.location, entity.destination}
                    == {world.actor.location_id, destination} and entity.open)
        assert act(world, "go", target=door)["outcome"] == "APPLIED"
        village.advance(world, 5)
    assert world.actor.location_id == order.source
    assert act(world, "village_return_cargo", wagon=False)["code"] == "CARGO_RETURNED"
    assert order.in_transit == 0 and order.returned_quantity == 1
    assert world.state.village.coins == coins
    assert world.state.village.stocks[stop.destination]["firewood"] == 0


def test_accepted_late_fuel_settles_once_at_physical_destination(tmp_path):
    world = make_world(tmp_path)
    plan = _fund_grouped_route(world)
    for _ in plan.stops:
        assert act(world, "village_execute_fuel_delivery", route_id=plan.route_id)["code"] == "FUEL_ROUTE_LOADED"
        village.advance(world, 5)
    stop = plan.stops[0]
    order = world.state.village.economy.orders[stop.order_id]
    world.state.village.minute = order.deadline + 1
    economy.advance(world)
    buyer = actor(world, order.buyer, stop.destination)
    assert "village_respond_late_fuel" in {item["id"] for item in buyer.actions()}
    assert act(buyer, "village_respond_late_fuel", order_id=order.id, accept=True)["code"] == "LATE_FUEL_ACCEPTED"
    coins_before = world.state.village.coins.copy()
    assert order.in_transit == 1 and order.delivered_quantity == 0
    for _ in range(12):
        if world.actor.location_id == stop.destination:
            break
        result = act(world, "village_execute_fuel_delivery", route_id=plan.route_id)
        assert result["code"] == "FUEL_ROUTE_TRAVEL", result
        village.advance(world, 5)
    assert world.actor.location_id == stop.destination
    request, revision = "accepted-late-fuel-once", world.revision()
    result = world.act(request, revision, "village_execute_fuel_delivery", {"route_id": plan.route_id})
    assert result["code"] == "FUEL_ROUTE_DELIVERED", result
    assert order.delivered_quantity == 1 and order.in_transit == 0
    assert world.state.village.stocks[stop.destination]["firewood"] == 1
    assert world.state.village.coins[order.buyer] < coins_before[order.buyer]
    coins_after = world.state.village.coins.copy()
    payments_after = [entry.model_dump() for entry in world.state.village.economy.payments]
    assert world.act(request, revision, "village_execute_fuel_delivery", {"route_id": plan.route_id}) == result
    assert world.state.village.stocks[stop.destination]["firewood"] == 1
    assert world.state.village.coins == coins_after
    assert [entry.model_dump() for entry in world.state.village.economy.payments] == payments_after
    world.save()
    reloaded = World(world.scenario, world.path, "firewood", "rowan", "session-rowan")
    assert reloaded.state.village.firewood.statuses[f"late:{order.id}"] == "accepted"
    assert reloaded.state.village.economy.orders[order.id].delivered_quantity == 1
    assert reloaded.state.village.stocks[stop.destination]["firewood"] == 1
    assert reloaded.state.village.coins == coins_after
    assert [entry.model_dump() for entry in reloaded.state.village.economy.payments] == payments_after


def test_fuel_route_action_rejects_non_owner_opt_out_and_blocked_passage(tmp_path):
    world = make_world(tmp_path)
    plan = _fund_grouped_route(world)
    action_id = "village_execute_fuel_delivery"
    other = actor(world, "jory")
    assert action_id not in {item["id"] for item in other.actions()}
    assert act(other, action_id, route_id=plan.route_id)["code"] == "ACTION_UNAVAILABLE"
    door = next(entity for entity in world.state.entities.values()
                if entity.kind == "door" and {entity.location, entity.destination}
                == {"woods", plan.stops[0].path[0]})
    door.open = False
    assert act(world, action_id, route_id=plan.route_id)["code"] == "FUEL_ROUTE_BLOCKED"
    assert world.state.village.stocks["woods"]["firewood"] == 12
    assert not world.state.village.residents["rowan"].shipments
    world.state.village.firewood.config.enabled = False
    assert action_id not in {item["id"] for item in world.actions()}
    assert act(world, action_id, route_id=plan.route_id)["code"] == "ACTION_UNAVAILABLE"


def test_execute_grouped_fuel_route_moves_real_cargo_and_settles_each_stop(tmp_path):
    world = make_world(tmp_path)
    plan = _fund_grouped_route(world)
    assert plan is not None and len(plan.stops) == 2
    coins = world.state.village.coins.copy()
    result = fuel.execute_delivery_plan(world, plan.route_id)
    assert result["status"] == "completed" and result["completed_stops"] == 2, result
    assert plan.remaining_inventory == 0
    assert world.actor.location_id == plan.stops[-1].destination
    assert world.state.village.stocks["woods"]["firewood"] == 10
    assert not world.state.village.residents["rowan"].shipments
    for stop in plan.stops:
        order = world.state.village.economy.orders[stop.order_id]
        assert order.status == "fulfilled" and order.delivered_quantity == 1
        assert world.state.village.stocks[stop.destination]["firewood"] == 1
        assert world.state.village.coins[order.buyer] == coins[order.buyer] - order.price - order.delivery_fee
    assert world.state.village.coins["rowan"] == coins["rowan"] + sum(
        world.state.village.economy.orders[stop.order_id].price +
        world.state.village.economy.orders[stop.order_id].delivery_fee for stop in plan.stops)
    assert fuel.execute_delivery_plan(world, plan.route_id)["status"] == "completed"


def test_execute_fuel_route_rechecks_stock_capacity_demand_and_travel(tmp_path):
    world = make_world(tmp_path)
    plan = _fund_grouped_route(world)
    initial = world.state.village.stocks["woods"]["firewood"]
    goods.take(world, "woods", "firewood", initial - 1)
    assert fuel.execute_delivery_plan(world, plan.route_id)["status"] == "blocked"
    assert plan.status == "planned" and not world.state.village.residents["rowan"].shipments
    goods.put(world, "woods", "firewood", initial - 1)
    world.actor.body.max_held_items = 1
    assert fuel.execute_delivery_plan(world, plan.route_id)["status"] == "blocked"
    world.actor.body.max_held_items = None
    stop = plan.stops[0]
    world.state.village.stocks[stop.destination]["firewood"] = 16
    assert fuel.execute_delivery_plan(world, plan.route_id)["status"] == "blocked"
    world.state.village.stocks[stop.destination]["firewood"] = 0
    door = next(entity for entity in world.state.entities.values()
                if entity.kind == "door" and {entity.location, entity.destination}
                == {"woods", stop.path[0]})
    door.open = False
    assert fuel.execute_delivery_plan(world, plan.route_id)["status"] == "blocked"
    assert world.actor.location_id == "woods" and not world.state.village.residents["rowan"].shipments
    door.open = True
    world.state.village.economy.orders[stop.order_id].status = "cancelled"
    assert fuel.execute_delivery_plan(world, plan.route_id)["status"] == "blocked"
    assert world.state.village.stocks["woods"]["firewood"] == initial


def test_execute_fuel_route_blocked_midway_preserves_paid_stops_and_cargo(tmp_path, monkeypatch):
    world = make_world(tmp_path)
    plan = _fund_grouped_route(world)
    original_act = world.act
    first_destination = plan.stops[0].destination
    def interrupt(*args, **kwargs):
        if (kwargs.get("action_id") == "go" and
                world.state.village.economy.orders[plan.stops[0].order_id].status == "fulfilled"):
            return {"outcome": "REJECTED", "code": "EXIT_BLOCKED", "explanation": "Road closed."}
        return original_act(*args, **kwargs)
    monkeypatch.setattr(world, "act", interrupt)
    result = fuel.execute_delivery_plan(world, plan.route_id)
    assert result["status"] == "blocked" and result["completed_stops"] == 1
    assert world.actor.location_id == first_destination
    assert plan.status == "in_progress" and plan.remaining_inventory == 1
    assert world.state.village.residents["rowan"].shipments[0].quantity == 1
    assert world.state.village.stocks[plan.stops[0].destination]["firewood"] == 1
    monkeypatch.setattr(world, "act", original_act)
    assert fuel.execute_delivery_plan(world, plan.route_id)["status"] == "completed"


def test_generator_opt_in_and_fallback_eligibility_follow_actual_fuel(tmp_path):
    legacy = build_scenario(scenario())
    assert legacy.village.firewood is None
    value = build_scenario(scenario(), firewood=True, anti_idle=True)
    assert value.village.firewood is not None
    world = make_world(tmp_path, minute=1080)
    world.scenario.village.anti_idle = True
    baker = actor(world, "jory")
    goods.put(world, "bakery", "flour", 2)
    assert not [row for row in village_fallback.candidates(baker) if row["action_id"] == "village_work"]
    assert act(baker, "village_work")["code"] == "FIREWOOD_REQUIRED"
    assert act(baker, "village_work")["code"] == "RETRY_SUPPRESSED"
    goods.put(world, "bakery", "firewood", 1)
    assert any(row["action_id"] == "village_work" for row in village_fallback.candidates(baker))
    assert act(baker, "village_work")["code"] == "BATCH_STARTED"
