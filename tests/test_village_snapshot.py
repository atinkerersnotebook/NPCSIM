from pathlib import Path

from npc_sim.models import Scenario
from npc_sim.storage import read_json
from npc_sim.village_economy_models import VillageSupplierOrder
from npc_sim.world import World
import pytest

from npc_sim import village, village_economy, village_goods, village_turns


ROOT = Path(__file__).resolve().parent.parent


def scenario() -> Scenario:
    return Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "millhaven-snapshot-i.json"))


def warehouse_world(tmp_path: Path) -> World:
    value = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "millhaven-warehouse-snapshot.json"))
    return World(value, tmp_path / "warehouse.json", "warehouse", "nella", "session-nella")


def world(tmp_path: Path) -> World:
    value = scenario()
    return World(value, tmp_path / "snapshot.json", "snapshot", "elsie", "session-elsie")


def complete_chain(item: World) -> None:
    economy = item.state.village.economy
    assert economy is not None
    for index, required in enumerate(item.scenario.village.snapshot.required_orders, 1):
        order = VillageSupplierOrder(
            id=f"order-{index}", request_key=f"snapshot-{index}", buyer=required.buyer,
            supplier=required.supplier, source=item.scenario.village.residents[required.supplier].work,
            destination=item.scenario.village.residents[required.buyer].work,
            product=required.product, quantity=required.quantity, price=1, delivery_fee=1,
            deadline=600, required_fresh_until=660, created_at=480,
            status="accepted_planned", production_plan={"finish": 570, "output": required.quantity})
        economy.orders[order.id] = order
    for resident in item.scenario.village.snapshot.required_plans:
        item.state.village.residents[resident].fallback.daily_plans.append({
            "day": 0, "created_at": 550, "orders": [{"buyer": resident, "supplier": "elsie"}]})


def test_snapshot_scenario_keeps_private_success_criteria_out_of_observation(tmp_path):
    item = world(tmp_path)
    observation = item.observe()
    assert "12 portions" in observation["narrative"]
    assert "required_orders" not in str(observation)
    assert "required_plans" not in str(observation)
    assert len(item.state.village.residents) == 12
    assert all(not person.life.hungry for person in item.state.village.residents.values())
    assert all(person.life.meals["0:breakfast"].status == "eaten"
               for person in item.state.village.residents.values())
    assert item.state.village.incidents is None


def test_warehouse_scenario_has_three_loaf_move_ladder_and_six_unit_requirement(tmp_path):
    item = warehouse_world(tmp_path)
    warehouse = item.scenario.village.warehouse
    task = next(task for task in warehouse.tasks if task.id == "pick-bread")
    assert task.quantity == task.minimum_quantity == 3
    assert warehouse.required_cart_load == warehouse.cart_capacity == 6
    assert "climb_ladder" in next(agent for agent in item.scenario.agents if agent.id == "jory").body.capabilities


def test_warehouse_task_change_checks_stock_and_bin_limits(tmp_path):
    from npc_sim import village_warehouse

    item = warehouse_world(tmp_path)
    state = item.state.village
    state.stocks["warehouse-bulk-b"]["bread"] = 5
    result = village_warehouse.apply(item, "village_request_warehouse_change", {
        "task": "pick-bread", "quantity": 3})
    assert result[0:2] == ("APPLIED", "WAREHOUSE_TASK_CHANGED")
    assert village_warehouse.task_quantity(item.scenario.village.warehouse, state, "pick-bread") == 3

    state.stocks["warehouse-bulk-b"]["bread"] = 4
    rejected = village_warehouse.apply(item, "village_request_warehouse_change", {
        "task": "pick-bread", "quantity": 3})
    assert rejected[0:2] == ("REJECTED", "MINIMUM_STOCK")


def test_warehouse_goal_requires_observed_six_unit_cart_load(tmp_path):
    from npc_sim import village_warehouse

    item = warehouse_world(tmp_path)
    state = item.state.village
    for task in item.scenario.village.warehouse.tasks:
        state.warehouse_task_progress[task.id] = task.quantity
    for location, rules in item.scenario.village.warehouse.stock_rules.items():
        for product, rule in rules.items():
            state.stocks[location][product] = max(rule.minimum, min(rule.maximum, state.stocks[location].get(product, 0)))

    active = village_warehouse.evaluate_goal(item)
    assert active["status"] == "active"
    assert not active["evidence"]["checks"]["required_cart_load_met"]

    state.warehouse_max_cart_load = 6
    achieved = village_warehouse.evaluate_goal(item)
    assert achieved["status"] == "achieved"


def test_warehouse_elevated_pick_loads_at_ladder_base_only_after_challenge(tmp_path):
    from npc_sim import village_warehouse

    item = warehouse_world(tmp_path)
    state = item.state.village
    item.actor.location_id = "warehouse-rack-base"
    cart = item.state.entities[item.scenario.village.warehouse.cart]
    cart.location = "warehouse-rack-base"
    state.warehouse_cart_driver = item.actor_id

    blocked = village_warehouse.apply(item, "village_load_cart", {
        "task": "elevated-fish", "quantity": 1})
    assert blocked[0:2] == ("REJECTED", "INVALID_CART_TASK")

    item.state.skill_uses["position-warehouse-ladder"] = item.actor_id
    loaded = village_warehouse.apply(item, "village_load_cart", {
        "task": "elevated-fish", "quantity": 1})
    assert loaded[0:2] == ("APPLIED", "WAREHOUSE_CART_LOADED")
    assert state.warehouse_cart_loads[0].source == "warehouse-overhead"


def test_warehouse_observation_shares_route_and_ladder_status(tmp_path):
    from npc_sim import village_warehouse

    item = warehouse_world(tmp_path)
    item.actor.location_id = "warehouse-bulk-a"
    item.state.skill_uses["position-warehouse-ladder"] = "ada"
    observed = village_warehouse.observation(item)
    fish = next(task for task in observed["tasks"] if task["id"] == "elevated-fish")
    assert fish["load_location"] == "warehouse-rack-base"
    assert fish["ladder_secured"] is True
    assert "Load at the ladder landing" in fish["load_instructions"]
    assert observed["suggested_cart_route"]["planned_units"] <= 6
    assert observed["suggested_cart_route"]["stops"]


def test_warehouse_actors_can_plan_and_follow_cart_routes(tmp_path):
    from npc_sim import village_walking, village_warehouse

    item = warehouse_world(tmp_path)
    assert village_walking.enabled(item)
    blocked = village_walking._path(item, "warehouse-bulk-b")
    assert blocked is not None
    planned = village_walking.plan(item, {
        "destination": "warehouse-bulk-b", "purpose": "Load pending warehouse stock"})
    assert planned[0:2] == ("APPLIED", "ROUTE_PLANNED")
    route = item.state.village.residents["nella"].walking_plan
    assert route is not None and route.destination == "warehouse-bulk-b"
    assert route.doors and village_walking.check_step(item, {"target": route.doors[0]}) is None
    assert route.destination == "warehouse-bulk-b"
    assert route.locations[-1] == "warehouse-bulk-b"
    observed = village_warehouse.observation(item)["suggested_cart_route"]
    assert observed["final_destination"] in {stop["source"] for stop in observed["stops"]}


def test_snapshot_demand_is_six_loaves_not_six_plus_stock_buffer(tmp_path):
    item = world(tmp_path)
    requirement = village_economy.meal_requirements(item)[0]
    suggestions = village_economy.suggestions(item, "elsie")
    bread = [request for request in suggestions if request["product"] == "bread"]

    assert requirement["portions"] == 12
    assert requirement["inputs"]["bread"] == 6
    assert bread and sum(request["quantity"] for request in bread) == 6
    assert item.scenario.village.snapshot.required_orders[0].quantity == 6
    assert {(item.buyer, item.supplier, item.product, item.quantity)
            for item in item.scenario.village.snapshot.required_orders} == {
                ("elsie", "jory", "bread", 6),
                ("elsie", "ada", "milk", 12),
                ("elsie", "ada", "eggs", 12),
            }
    assert item.scenario.village.economy.meal_plans[0].additional_inputs == {"milk": 12, "eggs": 12}
    assert item.scenario.village.residents["jory"].job.batch_size == 6
    assert item.scenario.village.residents["elsie"].job.batch_size == 3
    assert (item.scenario.village.residents["elsie"].job.batch_size *
            item.scenario.village.residents["elsie"].job.outputs["meals"]) == 6
    assert item.scenario.village.stocks["bakery"]["bread"] == 0
    assert item.scenario.village.stocks["general-store"]["bread"] == 0
    assert item.scenario.village.stocks["bakery"]["flour"] == 8


def test_snapshot_has_no_forecast_work_until_personal_order(tmp_path):
    from npc_sim import village_fallback, village_planning

    item = world(tmp_path)
    jory = item.bind("jory", "session-jory")

    assert village_planning._requirements(jory, "jory") == []
    assert not any(choice["action_id"] == "village_daily_plan"
                   for choice in village_fallback.candidates(jory))


def test_elsie_reads_brunch_notice_before_any_other_snapshot_work(tmp_path):
    from npc_sim import village_communication, village_fallback

    item = world(tmp_path)
    elsie = item.bind("elsie", "session-elsie")
    choices = village_fallback.candidates(elsie)

    assert item.state.village.communication.notices["snapshot-brunch-request"][0].terms.quantity is None
    assert choices[0]["task_id"] == "snapshot:read-brunch-request"
    assert "snapshot-brunch-request" not in item.state.village.communication.knowledge.get("elsie", {})
    result = village_communication._intent_request(
        elsie, "village_check_messages", village_communication.CheckMessagesIntent(channel="inbox"))
    assert result[0] == "NOTICES_READ"
    assert "snapshot-brunch-request" in item.state.village.communication.knowledge["elsie"]


def test_snapshot_supply_request_is_buyer_owned_and_has_no_crew_actions(tmp_path):
    from npc_sim import village_communication

    item = world(tmp_path)
    intruder = item.bind("nella", "session-nella")
    with pytest.raises(ValueError, match="Only elsie"):
        village_communication._intent_request(
            intruder, "village_request_supply", village_communication.SupplyIntent(
                good="bread", quantity=6, purpose="Duplicate brunch request", deadline=650,
                required_fresh_until=660, preferred_supplier_id="jory"))
    assert "village_propose_meeting" not in {action["id"] for action in item.actions()}
    assert "village_schedule_crew" not in {action["id"] for action in item.actions()}


def test_snapshot_needs_physical_stock_not_just_funded_orders(tmp_path):
    item = world(tmp_path)
    item.state.village.minute = 620
    complete_chain(item)
    result = item.evaluate_goal("village_snapshot_completed")
    assert result["status"] == "active"
    assert not result["evidence"]["checks"]["ingredients_at_tavern"]


def ready_to_cook(item: World) -> None:
    from npc_sim import village_planning

    village.advance(item, 80)
    village_goods.put(item, "tavern", "bread", 6)
    village_goods.put(item, "tavern", "milk", 12)
    village_goods.put(item, "tavern", "eggs", 12)
    village_planning.reconcile(item, "resident action")


@pytest.mark.parametrize("minute", [620, 660])
def test_snapshot_succeeds_before_cooking_without_supplier_plans(tmp_path, minute):
    item = world(tmp_path)
    ready_to_cook(item)
    village.advance(item, minute - 620)
    result = item.evaluate_goal("village_snapshot_completed")
    assert result["status"] == "achieved", result["evidence"]["cooking_blocker"]
    assert item.state.village.stocks["tavern"]["meals"] == 0
    assert item.act("cook", item.revision(), "village_work", {})["outcome"] == "APPLIED"


@pytest.mark.parametrize("blocker", ["stale_bread", "missing_fuel", "broken_hearth", "cook_away", "late"])
def test_snapshot_rejects_unready_brunch(tmp_path, blocker):
    item = world(tmp_path)
    ready_to_cook(item)
    state = item.state.village
    if blocker == "stale_bread":
        state.stock_lots["tavern"]["bread"][0].expires_at = 660
    elif blocker == "missing_fuel":
        village_goods.take(item, "tavern", "firewood", 12)
    elif blocker == "broken_hearth":
        state_entity = item.state.entities["tavern-hearth"]
        state_entity.health = 0
    elif blocker == "cook_away":
        item.actor.location_id = "bakery"
    else:
        state.minute = 665
    result = item.evaluate_goal("village_snapshot_completed")
    assert result["status"] == ("failed" if blocker == "late" else "active"), result


def test_snapshot_only_schedules_the_brunch_cast(tmp_path):
    item = world(tmp_path)
    for agent in item.scenario.agents:
        item.bind(agent.id, f"session-{agent.id}")
    assert len(item.state.actors) == 12
    assert {village_turns.next_actor(item) for _ in range(10)} == {"elsie", "jory", "mara", "nella", "ada"}


def test_snapshot_never_schedules_optional_meals(tmp_path):
    from npc_sim import village_fallback

    item = world(tmp_path)
    village.advance(item, 80)
    village_goods.put(item, "tavern", "meals", 20)
    for rid in ("mara", "ada", "nella"):
        actor = item.bind(rid, f"session-{rid}")
        actor.actor.location_id = "tavern"
        assert not any(choice["action_id"] == "village_eat"
                       for choice in village_fallback.candidates(actor))


def test_snapshot_elsie_can_request_every_brunch_input(tmp_path):
    from npc_sim import village_fallback
    from npc_sim.village_communication_models import NoticeKnowledge

    item = world(tmp_path)
    elsie = item.bind("elsie", "session-elsie")
    village_communication = item.state.village.communication
    initial = village_communication.notices["snapshot-brunch-request"][-1]
    village_communication.knowledge["elsie"] = {}
    village_communication.knowledge["elsie"][initial.id] = NoticeKnowledge(
        snapshot=initial.model_copy(deep=True), heard_at=item.state.village.minute,
        source_type="inbox", confidence="direct")

    choices = village_fallback.candidates(elsie)
    requests = [choice for choice in choices if choice["action_id"] == "village_request_supply"]

    assert {(choice["arguments"]["good"], choice["arguments"]["quantity"])
            for choice in requests} == {("bread", 6)} | {("milk", 12), ("eggs", 12)}


def test_snapshot_brunch_bundle_ignores_post_spam_cooldown(tmp_path):
    from npc_sim import village_communication

    item = world(tmp_path)
    elsie = item.bind("elsie", "session-elsie")
    item.state.village.communication.cooldowns["post:elsie"] = 600
    for product, quantity in (("bread", 6), ("milk", 12), ("eggs", 12)):
        outcome, code, _, _ = village_communication.apply(
            elsie, "village_request_supply", {
                "good": product, "quantity": quantity,
                "purpose": f"Brunch {product}", "deadline": 600 if product != "bread" else 630,
                "required_fresh_until": 660,
                "preferred_supplier_id": "jory" if product == "bread" else "ada"})
        assert (outcome, code) == ("APPLIED", "NOTICE_POSTED")


def test_snapshot_brunch_offers_next_input_after_first_post(tmp_path):
    from npc_sim import village_communication, village_fallback

    item = world(tmp_path)
    elsie = item.bind("elsie", "session-elsie")
    village_communication.apply(elsie, "village_check_messages", {"channel": "inbox"})
    result = village_communication.apply(elsie, "village_request_supply", {
        "good": "bread", "quantity": 6, "purpose": "Brunch bread",
        "deadline": 630, "required_fresh_until": 660, "preferred_supplier_id": "jory"})
    assert result[0] == "APPLIED"

    choices = village_fallback.candidates(elsie)
    assert any(choice["action_id"] == "village_request_supply"
               and choice["arguments"]["good"] == "milk" for choice in choices)


def test_snapshot_opening_brunch_request_is_private_to_elsie(tmp_path):
    item = world(tmp_path)

    elsie = item.bind("elsie", "session-elsie").observe()
    mara = item.bind("mara", "session-mara").observe()
    jory = item.bind("jory", "session-jory").observe()

    assert elsie["opening"] and "travelling party" in elsie["narrative"]
    assert not mara["opening"] and "brunch" not in mara["narrative"] and "brunch" not in mara["role"]
    assert not jory["opening"] and "brunch" not in jory["narrative"] and "brunch" not in jory["role"]


def test_snapshot_agent_goals_redact_private_brunch_request(tmp_path):
    from npc_sim.models import Scenario

    scenario = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "millhaven-snapshot-i.json"))
    goals = {
        agent.id: agent.role
        for agent in scenario.agents
    }
    assert "brunch" not in goals["mara"].casefold()
    assert "brunch" not in goals["jory"].casefold()
    assert "travelling party" in goals["elsie"].casefold()


def test_snapshot_jory_bakes_only_after_funded_bread_request(tmp_path):
    from npc_sim import village_communication, village_fallback

    item = world(tmp_path)
    jory = item.bind("jory", "session-jory")
    assert not any(choice["action_id"] == "village_work"
                   for choice in village_fallback.candidates(jory))

    elsie = item.bind("elsie", "session-elsie")
    village_communication.apply(elsie, "village_request_supply", {
        "good": "bread", "quantity": 6, "purpose": "Brunch bread",
        "deadline": 630, "required_fresh_until": 660, "preferred_supplier_id": "jory"})
    notice = next(reversed(item.state.village.communication.notices))
    result = village_communication.apply(jory, "village_respond_to_request", {
        "request_id": notice, "response": "accept"})
    assert result[0] == "APPLIED"
    assert any(choice["action_id"] == "village_work"
               for choice in village_fallback.candidates(jory))


def test_snapshot_accepts_split_orders_that_cover_required_quantity(tmp_path):
    item = world(tmp_path)
    economy = item.state.village.economy
    assert economy is not None
    item.scenario.village.snapshot.cooking_service = None
    complete_chain(item)
    del economy.orders["order-1"]
    for index, quantity in enumerate((5, 7), 1):
        order = VillageSupplierOrder(
            id=f"split-{index}", request_key=f"snapshot-split-{index}", buyer="elsie",
            supplier="jory", source="bakery", destination="tavern", product="bread",
            quantity=quantity, price=3, delivery_fee=1, deadline=600,
            required_fresh_until=660, created_at=550, status="accepted_planned",
            production_plan={"finish": 570, "output": quantity})
        economy.orders[order.id] = order
    result = item.evaluate_goal("village_snapshot_completed")
    bread = next(row for row in result["evidence"]["orders"] if row["product"] == "bread")
    assert bread["confirmed_quantity"] == 12
    assert bread["complete"]


def test_snapshot_accepts_order_quantity_beyond_one_carrier_capacity(tmp_path):
    item = world(tmp_path)
    for agent in item.scenario.agents:
        item.bind(agent.id, f"session-{agent.id}")
    required = next(order for order in item.scenario.village.snapshot.required_orders
                    if order.product == "bread")
    economy = item.state.village.economy
    assert economy is not None
    order = VillageSupplierOrder(
        id="large", request_key="snapshot-large", buyer=required.buyer,
        supplier=required.supplier, source="bakery", destination="tavern",
        product="bread", quantity=required.quantity, price=3, delivery_fee=1,
        deadline=630, required_fresh_until=660, created_at=540)
    village_goods.put(item, "bakery", "bread", 2)
    feasible, reason = village_economy.production_plan(item, order)
    assert feasible and not reason


def test_snapshot_fails_after_deadline_with_incomplete_chain(tmp_path):
    item = world(tmp_path)
    item.state.village.minute = 665
    result = item.evaluate_goal("village_snapshot_completed")
    assert result["status"] == "failed"
    assert not result["evidence"]["checks"]["before_deadline"]


def test_legacy_snapshot_remains_achieved_when_verified_after_deadline(tmp_path):
    item = world(tmp_path)
    item.scenario.village.snapshot.cooking_service = None
    complete_chain(item)
    item.state.village.minute = 665
    result = item.evaluate_goal("village_snapshot_completed")
    assert result["status"] == "achieved", result
    assert all(result["evidence"]["checks"].values())


def test_warehouse_snapshot_exposes_submap_cart_and_ladder_constraint(tmp_path):
    value = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "millhaven-warehouse-snapshot.json"))
    item = World(value, tmp_path / "warehouse.json", "warehouse", "nella", "session-nella")

    observation = item.observe()
    action_ids = {action["id"] for action in observation["actions"]}
    assert item.scenario.village.warehouse is not None
    assert observation["village"]["warehouse"]["cart"] == "warehouse-cart"
    assert "village_drive_cart" in action_ids
    assert any(entity["id"] == "receiving-cross-aisle" for entity in observation["visible_entities"])

    elevator = item.scenario.entities["rack-ladder-access"]
    assert elevator.required_challenges == ["position-warehouse-ladder"]


def test_warehouse_cart_preserves_stock_rules_and_completes_task(tmp_path):
    value = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "millhaven-warehouse-snapshot.json"))
    item = World(value, tmp_path / "warehouse.json", "warehouse", "nella", "session-nella")

    assert item.act("warehouse-drive", item.revision(), "village_drive_cart", {"drive": True})["outcome"] == "APPLIED"
    village.advance(item, 5)
    assert item.act("warehouse-load", item.revision(), "village_load_cart",
                   {"task": "putaway-flour", "quantity": 3})["outcome"] == "APPLIED"
    assert item.state.village.stocks["warehouse-receiving"]["flour"] == 0

    item.actor.location_id = "warehouse-bulk-a"
    item.state.entities["warehouse-cart"].location = "warehouse-bulk-a"
    village.advance(item, 5)
    assert item.act("warehouse-unload", item.revision(), "village_unload_cart", {})["outcome"] == "APPLIED"
    result = item.evaluate_goal("village_snapshot_completed")
    task = next(row for row in result["evidence"]["tasks"] if row["id"] == "putaway-flour")

    assert task["complete"]
    assert item.state.village.stocks["warehouse-bulk-a"]["flour"] == 3



