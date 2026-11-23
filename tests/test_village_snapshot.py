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
    assert "24 portions" in observation["narrative"]
    assert "required_orders" not in str(observation)
    assert "required_plans" not in str(observation)
    assert len(item.state.village.residents) == 12


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
    village_goods.put(item, "tavern", "bread", 12)
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
    assert not feasible and not reason


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



