import json

import pytest
from pydantic import ValidationError

from npc_sim import village, village_economy as economy, village_goods as goods
from npc_sim.models import Scenario, VillageCommittedWork, VillageMeal, VillageStockLot
from npc_sim.village_economy_models import VillageEconomyConfig, VillageEconomyState, VillageMealPlan
from npc_sim.world import World
from test_village_expanded import scenario
from test_world import act


@pytest.fixture
def world(tmp_path):
    value = scenario()
    value.village.economy = VillageEconomyConfig()
    value.village.start_minute = 480
    value.village.goal_deadlines = {}
    value.village.coins["elsie"] = 1000
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
    result = World(value, tmp_path / "economy.json", "economy", "elsie", "session-elsie")
    for agent in value.agents:
        result.bind(agent.id, f"session-{agent.id}")
    for location, stock in result.state.village.stocks.items():
        for product in stock:
            stock[product] = 0
    result.state.village.stock_lots = {}
    return result


def actor(world, rid, location=None):
    bound = world.bind(rid, f"session-{rid}")
    if location:
        bound.actor.location_id = location
    return bound


def add(world, location, product, quantity, expiry=None, produced=480):
    goods.put(world, location, product, quantity,
              [VillageStockLot(quantity=quantity, produced_at=produced, expires_at=expiry)],
              kind="imported")


def propose(world, product="bread", quantity=3, supplier="jory", deadline=1110, fresh=1140, **extra):
    location = world.actor.location_id
    world.actor.location_id = world.state.actors[supplier].location_id
    result = economy.apply(world, "village_propose_order", {
        "product": product, "quantity": quantity, "supplier": supplier,
        "deadline": deadline, "required_fresh_until": fresh, **extra})
    world.actor.location_id = location
    assert result[0] == "APPLIED", result
    return next(reversed(world.state.village.economy.orders.values()))


def accept(world, order):
    result = economy.apply(actor(world, order.supplier), "village_accept_order", {"order_id": order.id})
    assert result[0] == "APPLIED", result


def test_standalone_strict_models_and_legacy_opt_out(world):
    with pytest.raises(ValidationError):
        VillageEconomyConfig(unexpected=True)
    with pytest.raises(ValidationError):
        VillageEconomyState(unknown=[])
    with pytest.raises(ValidationError):
        VillageEconomyConfig(stock_buffer={"bread": True})
    with pytest.raises(ValidationError):
        VillageMealPlan(id="supper", use_minute=1140, portions=4, recipe_portions={"bread": 4, "fish": 4})
    legacy = scenario()
    assert economy.initial_state(legacy) is None
    legacy.village.economy = VillageEconomyConfig()
    assert economy.initial_state(legacy) is not None


def test_alternative_recipes_fresh_stock_and_incoming_reduce_demand(world):
    add(world, "tavern", "bread", 2, expiry=1140)
    add(world, "tavern", "bread", 1, expiry=1200)
    requests = {request["product"]: request for request in economy.suggestions(world)}
    assert requests["bread"]["quantity"] == 3
    assert requests["fish"]["quantity"] == 2
    add(world, "bakery", "bread", 2, expiry=1200)
    order = propose(world, quantity=2)
    accept(world, order)
    requests = {request["product"]: request for request in economy.suggestions(world)}
    assert requests["bread"]["quantity"] == 1
    assert requests["bread"]["confirmed_incoming"] == 2


def test_ready_meals_and_committed_cooking_reduce_ingredient_orders(world):
    add(world, "tavern", "meals", 4, expiry=1300)
    world.state.village.residents["elsie"].life.work = VillageCommittedWork(
        location="tavern", finishes_at=1100, outputs={"meals": 4})
    requests = {request["product"]: request for request in economy.suggestions(world)}
    assert "bread" not in requests
    assert requests["fish"]["quantity"] == 2


def test_planning_is_idempotent_and_persistent(world):
    first = economy.apply(world, "village_plan_supplies", {})
    assert first[0] == "APPLIED"
    assert len(world.state.village.economy.orders) == 0
    for supplier in ("jory", "finn"):
        world.actor.location_id = world.state.actors[supplier].location_id
        assert economy.apply(world, "village_plan_supplies", {"supplier": supplier})[0] == "APPLIED"
    assert len(world.state.village.economy.orders) == 2
    assert economy.apply(world, "village_plan_supplies", {})[0] == "APPLIED"
    assert len(world.state.village.economy.orders) == 2
    world.save()
    restored = World(world.scenario, world.path, "economy", "elsie", "session-elsie")
    assert economy.apply(restored, "village_plan_supplies", {})[0] == "APPLIED"
    assert economy.apply(restored, "village_plan_supplies", {"supplier": "finn"})[0] == "APPLIED"
    assert len(restored.state.village.economy.orders) == 2


def test_fees_and_existing_commitments_are_affordable_before_acceptance(world):
    world.state.village.coins["elsie"] = 11
    add(world, "bakery", "bread", 2, expiry=1400)
    first = propose(world, quantity=2)
    accept(world, first)
    actor(world, "finn", world.actor.location_id)
    result = economy.apply(world, "village_propose_order", {
        "product": "fish", "quantity": 2, "supplier": "finn", "deadline": 1110,
        "required_fresh_until": 1140})
    assert result[:2] == ("REJECTED", "INSUFFICIENT_COINS")
    assert list(world.state.village.economy.orders.values())[-1].status == "rejected"
    assert economy.commitments(world, "elsie") == 8


def test_acceptance_checks_spending_since_proposal_and_records_rejection(world):
    order = propose(world)
    world.state.village.coins["elsie"] = 3
    result = economy.apply(actor(world, "jory"), "village_accept_order", {"order_id": order.id})
    assert result[:2] == ("REJECTED", "INSUFFICIENT_COINS")
    assert order.status == "rejected"
    assert any(event.action == "order_rejected" for event in world.state.village.events)


def test_no_unsolicited_load_or_overdelivery_and_capacity_is_real(world):
    add(world, "bakery", "bread", 5, expiry=1400)
    carrier = actor(world, "nella", "bakery")
    loads = carrier.state.village.residents["nella"].shipments
    assert "ORDER_REQUIRED" in goods.load(carrier, "nella", {"bread": 1}, "tavern", loads, 3)
    order = propose(world, quantity=2)
    accept(world, order)
    assert "capacity" in goods.load(carrier, "nella", {"bread": 4}, "tavern", loads, 3)
    assert "ORDER_REQUIRED" in goods.load(carrier, "nella", {"bread": 3}, "tavern", loads, 3)
    assert loads == [] and carrier.state.village.stocks["bakery"]["bread"] == 5
    assert goods.load(carrier, "nella", {"bread": 2}, "tavern", loads, 3) is None
    assert loads[0].order_id == order.id and order.in_transit == 2


def test_partial_deliveries_payment_once_and_conserved_cash_and_stock(world):
    add(world, "bakery", "bread", 3, expiry=1400)
    order = propose(world)
    accept(world, order)
    carrier = actor(world, "nella", "bakery")
    loads = carrier.state.village.residents["nella"].shipments
    before = sum(carrier.state.village.coins.values())
    assert goods.load(carrier, "nella", {"bread": 2}, "tavern", loads, 3) is None
    carrier.actor.location_id = "tavern"
    assert goods.unload(carrier, "nella", loads) is None
    assert (order.delivered_quantity, order.in_transit, order.remaining, order.status) == (2, 0, 1, "accepted")
    payments = len(carrier.state.village.economy.payments)
    assert goods.unload(carrier, "nella", loads) is not None
    assert len(carrier.state.village.economy.payments) == payments
    carrier.actor.location_id = "bakery"
    assert goods.load(carrier, "nella", {"bread": 1}, "tavern", loads, 3) is None
    carrier.actor.location_id = "tavern"
    assert goods.unload(carrier, "nella", loads) is None
    assert order.status == "fulfilled" and order.paid == 9 and order.fees_paid == 2
    assert sum(carrier.state.village.coins.values()) == before
    assert carrier.state.village.stocks["tavern"]["bread"] == 3


def test_blocked_production_allows_previously_loaded_partial_delivery(world):
    add(world, "bakery", "bread", 3, expiry=1400)
    add(world, "bakery", "flour", 1, expiry=1400)
    order = propose(world, quantity=4, deadline=1080)
    accept(world, order)
    assert order.status == "accepted_planned"
    carrier = actor(world, "jory", "bakery")
    loads = carrier.state.village.residents["jory"].shipments
    assert goods.load(carrier, "jory", {"bread": 3}, "tavern", loads, 3) is None
    carrier.actor.location_id = "tavern"
    carrier.state.village.minute = 570
    economy.advance(carrier)
    assert order.status == "blocked"
    result = carrier.act("planned-partial-unload", carrier.observe()["observation_revision"],
                         "village_unload_goods", {})
    assert (result["outcome"], result["code"]) == ("APPLIED", "GOODS_TRANSFERRED")
    assert (order.delivered_quantity, order.remaining, order.in_transit) == (3, 1, 0)
    assert loads == []


def test_blocked_order_still_rejects_late_cargo(world):
    add(world, "bakery", "bread", 3, expiry=1400)
    add(world, "bakery", "flour", 1, expiry=1400)
    order = propose(world, quantity=4, deadline=600)
    accept(world, order)
    carrier = actor(world, "jory", "bakery")
    loads = carrier.state.village.residents["jory"].shipments
    assert goods.load(carrier, "jory", {"bread": 3}, "tavern", loads, 3) is None
    carrier.actor.location_id = "tavern"
    carrier.state.village.minute = 605
    order.status = "blocked"
    assert "ORDER_CLOSED" in goods.unload(carrier, "jory", loads)
    assert order.delivered_quantity == 0 and order.in_transit == 3


def test_blocked_production_recovers_after_real_stock_arrives_without_new_order(world):
    add(world, "bakery", "bread", 3, expiry=1400)
    add(world, "bakery", "flour", 1, expiry=1400)
    order = propose(world, quantity=4, deadline=1080)
    accept(world, order)
    carrier = actor(world, "jory", "bakery")
    loads = carrier.state.village.residents["jory"].shipments
    assert goods.load(carrier, "jory", {"bread": 3}, "tavern", loads, 3) is None
    carrier.state.village.minute = 570
    economy.advance(carrier)
    assert order.status == "blocked"
    carrier.actor.location_id = "tavern"
    assert goods.unload(carrier, "jory", loads) is None
    assert (order.delivered_quantity, order.paid, order.remaining) == (3, 9, 1)
    add(world, "bakery", "bread", 1, expiry=1400, produced=575)
    carrier.actor.location_id = "bakery"
    economy.advance(carrier)
    assert order.status == "ready_to_collect"
    assert goods.load(carrier, "jory", {"bread": 1}, "tavern", loads, 3) is None
    carrier.actor.location_id = "tavern"
    assert goods.unload(carrier, "jory", loads) is None
    assert (order.status, order.delivered_quantity, order.paid, order.remaining) == ("fulfilled", 4, 12, 0)
    assert len([item for item in carrier.state.village.economy.orders.values() if item.id == order.id]) == 1
    assert carrier.state.village.stocks["tavern"]["bread"] == 4


def test_planned_and_blocked_orders_keep_buyer_funds_reserved(world):
    add(world, "bakery", "bread", 3, expiry=1400)
    add(world, "bakery", "flour", 1, expiry=1400)
    order = propose(world, quantity=4, deadline=1080)
    accept(world, order)
    reserved = order.remaining * (order.price + order.delivery_fee)
    assert economy.commitments(world, "elsie") == reserved
    carrier = actor(world, "jory", "bakery")
    assert goods.load(carrier, "jory", {"bread": 3}, "tavern",
                      carrier.state.village.residents["jory"].shipments, 3) is None
    carrier.state.village.minute = 570
    economy.advance(carrier)
    assert order.status == "blocked"
    assert economy.commitments(world, "elsie") == reserved
    carrier.state.village.coins["elsie"] = 3 * order.price + order.delivery_fee - 1
    carrier.actor.location_id = "tavern"
    assert "INSUFFICIENT_COINS" in goods.unload(
        carrier, "jory", carrier.state.village.residents["jory"].shipments)
    assert order.delivered_quantity == 0


def test_consolidated_orders_keep_accounting_and_charge_one_trip_fee(world):
    add(world, "bakery", "bread", 3, expiry=1400)
    first = propose(world, quantity=1, request_key="first")
    second = propose(world, quantity=2, request_key="second")
    accept(world, first)
    accept(world, second)
    carrier = actor(world, "nella", "bakery")
    loads = carrier.state.village.residents["nella"].shipments
    assert goods.load(carrier, "nella", {"bread": 3}, "tavern", loads, 3) is None
    assert len(loads) == 2
    carrier.actor.location_id = "tavern"
    assert goods.unload(carrier, "nella", loads) is None
    assert first.status == second.status == "fulfilled"
    assert first.paid + second.paid == 9
    assert first.fees_paid + second.fees_paid == 1


def test_stale_selection_is_fefo_not_produced_fifo_and_failure_is_atomic(world):
    add(world, "bakery", "bread", 1, expiry=1400, produced=480)
    add(world, "bakery", "bread", 1, expiry=1200, produced=500)
    selected = goods.take(world, "bakery", "bread", 1)
    assert selected[0].expires_at == 1200
    with pytest.raises(ValueError, match="fresh"):
        goods.take(world, "bakery", "bread", 1, required_fresh_until=1400)
    assert world.state.village.stocks["bakery"]["bread"] == 1


def test_stale_order_load_is_rejected_before_moving_stock(world):
    add(world, "bakery", "bread", 3, expiry=1400)
    order = propose(world)
    accept(world, order)
    goods.take(world, "bakery", "bread", 3)
    add(world, "bakery", "bread", 3, expiry=1140)
    carrier = actor(world, "nella", "bakery")
    loads = carrier.state.village.residents["nella"].shipments
    assert "STALE_GOODS" in goods.load(carrier, "nella", {"bread": 3}, "tavern", loads, 3)
    assert loads == [] and order.in_transit == 0
    assert carrier.state.village.stocks["bakery"]["bread"] == 3


def test_failed_payment_preserves_cargo_and_order_progress(world):
    add(world, "bakery", "bread", 3, expiry=1400)
    order = propose(world)
    accept(world, order)
    carrier = actor(world, "nella", "bakery")
    loads = carrier.state.village.residents["nella"].shipments
    assert goods.load(carrier, "nella", {"bread": 3}, "tavern", loads, 3) is None
    carrier.actor.location_id = "tavern"
    carrier.state.village.coins["elsie"] = 0
    assert "cannot pay" in goods.unload(carrier, "nella", loads)
    assert order.delivered_quantity == 0 and order.in_transit == 3
    assert len(loads) == 1 and carrier.state.village.stocks["tavern"]["bread"] == 0


@pytest.mark.parametrize("cancel", [True, False])
def test_cancel_or_deadline_failure_requires_real_cargo_return(world, cancel):
    add(world, "bakery", "bread", 3, expiry=1400)
    order = propose(world)
    accept(world, order)
    carrier = actor(world, "nella", "bakery")
    loads = carrier.state.village.residents["nella"].shipments
    assert goods.load(carrier, "nella", {"bread": 3}, "tavern", loads, 3) is None
    carrier.actor.location_id = "tavern"
    if cancel:
        assert economy.apply(world, "village_cancel_order", {"order_id": order.id})[0] == "APPLIED"
    else:
        carrier.state.village.minute = 1115
        economy.advance(carrier)
    assert order.status == ("cancelled" if cancel else "failed")
    assert "ORDER_CLOSED" in goods.unload(carrier, "nella", loads)
    assert economy.apply(carrier, "village_return_cargo", {"wagon": False})[0] == "REJECTED"
    carrier.actor.location_id = "bakery"
    assert economy.apply(carrier, "village_return_cargo", {"wagon": False})[0] == "APPLIED"
    assert loads == [] and order.in_transit == 0 and order.returned_quantity == 3
    assert carrier.state.village.stocks["bakery"]["bread"] == 3


def test_cargo_spoilage_releases_outstanding_units_and_records_real_loss(world):
    add(world, "bakery", "bread", 3, expiry=565)
    order = propose(world, deadline=550, fresh=560)
    accept(world, order)
    carrier = actor(world, "nella", "bakery")
    loads = carrier.state.village.residents["nella"].shipments
    assert goods.load(carrier, "nella", {"bread": 3}, "tavern", loads, 3) is None
    carrier.state.village.minute = 565
    goods.advance(carrier)
    economy.advance(carrier)
    assert loads == [] and order.in_transit == 0 and order.spoiled_quantity == 3
    assert order.status == "failed"
    assert any(item.kind == "spoiled" and item.order_id == order.id
               for item in carrier.state.village.economy.movements)


def test_supplier_target_counts_work_in_progress_and_preserves_raw_material(world):
    supplier = actor(world, "jory")
    add(world, "bakery", "flour", 5)
    assert economy.production_blocker(supplier, "village_work")[0] == "DEMAND_MET"
    add(world, "bakery", "bread", 3, expiry=1400)
    order = propose(world, quantity=3)
    accept(world, order)
    goods.take(world, "bakery", "bread", 3)
    supplier.state.village.economy.config.reserve_targets = {"bakery": {"flour": 5}}
    assert economy.production_blocker(supplier, "village_work") is None
    add(world, "bakery", "bread", 2, expiry=1400)
    assert economy.production_batch_limit(supplier, "village_work") == 1
    supplier.state.village.residents["jory"].life.work = VillageCommittedWork(
        location="bakery", finishes_at=510, outputs={"bread": 1})
    assert economy.production_blocker(supplier, "village_work")[0] == "DEMAND_MET"


def test_inn_only_cooks_near_service_and_respects_recipe_alternatives(world):
    assert economy.production_blocker(world, "village_work")[0] == "OUTSIDE_PREPARATION_WINDOW"
    world.state.village.minute = 1080
    assert economy.production_batch_limit(world, "village_work") == 2
    add(world, "tavern", "meals", 8, expiry=1300, produced=1080)
    assert economy.production_blocker(world, "village_work")[0] == "DEMAND_MET"
    assert economy.production_batch_limit(world, "village_cook_fish") == 2
    add(world, "tavern", "meals", 4, expiry=1300, produced=1080)
    assert economy.production_blocker(world, "village_cook_fish")[0] == "DEMAND_MET"


def test_inn_can_finish_successive_real_batches_before_supper(world):
    state = world.state.village
    state.economy.config.meal_plans = [VillageMealPlan(
        id="supper", use_minute=1140, portions=12, recipe_portions={"bread": 12})]
    state.minute = 1080
    add(world, "tavern", "bread", 6, expiry=1200, produced=1080)
    for batch in range(3):
        result = act(world, "village_work")
        assert result["outcome"] == "APPLIED", result
        assert state.stocks["tavern"]["meals"] == (batch + 1) * 4
        assert state.stocks["tavern"]["bread"] == 6 - (batch + 1) * 2
        if batch < 2:
            village.advance(world, 5)
    assert state.minute < 1140
    assert economy.meal_readiness(world)[0]["projected_unserved"] == 0


def test_inn_can_cook_final_two_at_1805_when_inputs_and_time_remain(world):
    state = world.state.village
    state.economy.config.meal_plans = [VillageMealPlan(
        id="supper", use_minute=1140, portions=12, recipe_portions={"bread": 12})]
    state.minute = 1085
    add(world, "tavern", "meals", 10, expiry=1200, produced=1080)
    add(world, "tavern", "bread", 1, expiry=1200, produced=1080)
    result = act(world, "village_work")
    assert result["outcome"] == "APPLIED", result
    assert state.stocks["tavern"]["meals"] == 12
    assert state.stocks["tavern"]["bread"] == 0


def test_seeded_inventory_reconciles_across_seven_persisted_days(tmp_path):
    from npc_sim.storage import read_json
    from test_village_expanded import ROOT

    value = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "millhaven-living.json"))
    path = tmp_path / "seven-days.json"
    village_world = World(value, path, "seven-days", "elsie", "session-elsie")
    state = village_world.state.village
    opening = {location: stock.copy() for location, stock in state.stocks.items()}
    assert economy.reconcile_inventory(village_world) == []

    for day in range(1, 8):
        village.advance(village_world, 1440)
        village_world.save()
        village_world = World(value, path, "seven-days", "elsie", "session-elsie")
        state = village_world.state.village
        assert state.minute == value.village.start_minute + day * 1440
        assert economy.reconcile_inventory(village_world) == []
        assert all(amount >= 0 for stock in state.stocks.values() for amount in stock.values())
    assert state.initial_stock_seed_id == value.village.initial_stock_seed_id
    assert state.stocks != opening


def test_seeded_inventory_reconciliation_detects_unexplained_stock(tmp_path):
    from npc_sim.storage import read_json
    from test_village_expanded import ROOT

    value = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "millhaven-living.json"))
    village_world = World(value, tmp_path / "audit.json", "audit", "elsie", "session-elsie")
    village_world.state.village.stocks["tavern"]["bread"] += 1
    assert economy.reconcile_inventory(village_world) == [{
        "location": "tavern", "product": "bread",
        "expected": value.village.stocks["tavern"]["bread"],
        "actual": value.village.stocks["tavern"]["bread"] + 1}]


def test_runtime_policy_persists_without_mutating_scenario(world):
    original = world.scenario.village.economy.model_dump()
    economy.configure(world, {**original, "meal_price": 2})
    assert world.scenario.village.economy.model_dump() == original
    assert economy.meal_policy(world) == (2, None, 0)
    world.save()
    restored = World(world.scenario, world.path, "economy", "elsie", "session-elsie")
    assert economy.meal_policy(restored) == (2, None, 0)
    order = propose(restored)
    with pytest.raises(ValueError, match="outstanding"):
        economy.configure(restored, original)
    assert order.status == "proposed"


def test_meal_payments_and_funded_subsidy_are_atomic_and_explicit(world):
    economy.configure(world, {**economy.config(world).model_dump(), "meal_price": 2,
                              "subsidy_payer": "ada", "subsidy_per_meal": 1})
    world.state.village.coins["ada"] = 0
    before = world.state.village.coins.copy()
    assert not economy.meal_charge(world, "jory")
    assert world.state.village.coins == before
    world.state.village.coins["ada"] = 5
    assert economy.meal_charge(world, "jory")
    assert world.state.village.coins["elsie"] == before["elsie"] + 3
    assert [item.kind for item in world.state.village.economy.payments] == ["meal", "subsidy"]


def test_reports_distinguish_transfers_production_consumption_and_inventory_value(world):
    goods.put(world, "bakery", "bread", 2)
    order = propose(world, quantity=2)
    accept(world, order)
    carrier = actor(world, "nella", "bakery")
    loads = carrier.state.village.residents["nella"].shipments
    assert goods.load(carrier, "nella", {"bread": 2}, "tavern", loads, 3) is None
    runs = economy.delivery_runs(carrier)
    assert runs == []
    carrier.actor.location_id = "tavern"
    assert goods.unload(carrier, "nella", loads) is None
    goods.take(carrier, "tavern", "bread", 1)
    seller_report = economy.observation(actor(world, "jory"))
    report = economy.observation(carrier)
    assert seller_report["products"]["bakery"]["bread"]["produced"] == 2
    assert "produced" not in report["products"]["tavern"]["bread"]
    assert report["products"]["tavern"]["bread"]["delivered"] == 2
    assert report["products"]["tavern"]["bread"]["consumed"] == 1
    assert report["inventory"]["tavern"]["bread"]["inventory_value"] == 3
    assert "not profit" in report["valuation_basis"]
    assert seller_report["cash"]["jory"]["gross_sales"] == 6
    assert report["cash"]["nella"]["delivery_fees_received"] == 1


def test_defer_authority_and_retry_acceptance(world):
    order = propose(world)
    assert economy.apply(world, "village_accept_order", {"order_id": order.id})[1] == "ORDER_AUTHORITY"
    supplier = actor(world, "jory")
    assert economy.apply(supplier, "village_defer_order", {"order_id": order.id, "until": 600})[0] == "APPLIED"
    assert economy.apply(supplier, "village_accept_order", {"order_id": order.id})[1] == "ORDER_DEFERRED"
    world.state.village.minute = 600
    add(world, "bakery", "bread", 3, expiry=1400, produced=600)
    accept(world, order)
    before = economy.commitments(world, "elsie")
    assert economy.apply(supplier, "village_accept_order", {"order_id": order.id})[1] == "ORDER_ALREADY_ACCEPTED"
    assert economy.commitments(world, "elsie") == before


def test_load_allocates_multiple_freshness_deadlines_atomically(world):
    add(world, "bakery", "bread", 1, expiry=1200)
    add(world, "bakery", "bread", 1, expiry=1400)
    first = propose(world, quantity=1, deadline=1110, fresh=1140, request_key="early")
    second = propose(world, quantity=1, deadline=1200, fresh=1250, request_key="later")
    accept(world, first)
    accept(world, second)
    carrier = actor(world, "nella", "bakery")
    loads = carrier.state.village.residents["nella"].shipments
    assert goods.load(carrier, "nella", {"bread": 2}, "tavern", loads, 3) is None
    assert {item.order_id: item.lots[0].expires_at for item in loads} == {
        first.id: 1200, second.id: 1400}


def test_opt_in_supper_forecast_requires_fresh_funded_physical_delivery(world):
    from npc_sim import village_extensions
    from npc_sim.models import VillageDayPolicy

    add(world, "bakery", "bread", 4, expiry=1200)
    assert economy.meal_delivery_forecast(world) == []
    world.state.village.day_policy = VillageDayPolicy(purposeful_downtime=True)
    forecast = economy.meal_delivery_forecast(world)[0]
    assert forecast["shortfall"] == 12
    assert forecast["delivery"]["source"] == "bakery"
    assert forecast["delivery"]["destination"] == "tavern"
    assert forecast["delivery"]["quantity"] == 4
    assert forecast["delivery"]["arrival"] <= forecast["delivery_deadline"]
    assert world.state.village.stocks["bakery"]["bread"] == 4
    world.state.village.coins["elsie"] = 0
    assert economy.meal_delivery_forecast(world)[0]["delivery"] is None
    assert economy.meal_delivery_forecast(world)[0]["blocker"]
    assert village_extensions.policy(world).purposeful_downtime


def test_opt_in_blocked_order_replans_only_with_real_stock_and_route(world):
    from npc_sim.models import VillageDayPolicy

    add(world, "bakery", "bread", 2, expiry=1400)
    order = propose(world, quantity=2)
    accept(world, order)
    goods.take(world, "bakery", "bread", 2)
    order.status, order.reason, order.production_plan = "blocked", "Planned production did not complete", {}
    economy.advance(world)
    assert order.status == "blocked"
    world.state.village.day_policy = VillageDayPolicy(purposeful_downtime=True)
    economy.advance(world)
    assert order.status == "blocked" and order.reason
    add(world, "bakery", "bread", 2, expiry=1400)
    economy.advance(world)
    assert order.status == "ready_to_collect"
    assert order.production_plan == {} and order.reason is None
    assert world.state.village.stocks["bakery"]["bread"] == 2


def test_opt_in_accepted_bread_order_exposes_physical_load_and_delivery_steps(world):
    from npc_sim.models import VillageDayPolicy

    world.state.village.day_policy = VillageDayPolicy(purposeful_downtime=True)
    add(world, "bakery", "bread", 2, expiry=1400)
    order = propose(world, quantity=2)
    accept(world, order)
    carrier = actor(world, "nella", "bakery")
    carrier.state.village.residents["nella"].known_routes = list(
        eid for eid, door in carrier.state.entities.items() if door.kind == "door")
    steps = economy.meal_delivery_actions(carrier)
    assert any(step["action"] == "village_load_goods" and step["order_id"] == order.id for step in steps)
    shipment = carrier.state.village.residents["nella"].shipments
    assert goods.load(carrier, "nella", {"bread": 2}, "tavern", shipment, 3) is None
    steps = economy.meal_delivery_actions(carrier)
    assert any(step["action"] == "go" and step["destination"] == "tavern" for step in steps)
    carrier.actor.location_id = "tavern"
    assert economy.meal_delivery_actions(carrier)[0]["action"] == "village_unload_goods"
    assert goods.unload(carrier, "nella", shipment) is None
    assert carrier.state.village.stocks["bakery"]["bread"] == 0
    assert carrier.state.village.stocks["tavern"]["bread"] == 2


def test_supper_procurement_is_early_and_only_requests_real_available_stock(world):
    from npc_sim.models import VillageDayPolicy

    world.state.village.day_policy = VillageDayPolicy(purposeful_downtime=True)
    add(world, "bakery", "bread", 2, expiry=1200)
    add(world, "bakery", "bread", 5, expiry=1130)
    requests = [request for request in economy.suggestions(world) if request["product"] == "bread"]
    assert len(requests) == 1
    assert requests[0]["quantity"] == 2
    assert requests[0]["deadline"] == 1080
    assert requests[0]["required_fresh_until"] == 1140
    assert world.state.village.stocks["bakery"]["bread"] == 7
    goods.take(world, "bakery", "bread", 2, required_fresh_until=1140)
    assert not [request for request in economy.suggestions(world) if request["product"] == "bread"]


def test_supper_forecast_does_not_request_bread_already_at_inn(world):
    from npc_sim.models import VillageDayPolicy

    world.state.village.day_policy = VillageDayPolicy(purposeful_downtime=True)
    add(world, "tavern", "bread", 5, expiry=1200)
    add(world, "bakery", "bread", 3, expiry=1200)
    forecast = economy.meal_delivery_forecast(world)[0]
    assert forecast["bread_needed"] == 0
    assert forecast["delivery"] is None
    assert forecast["shortfall"] > 0


def test_fulfilled_or_depleted_bread_order_never_suggests_another_load(world):
    from npc_sim.models import VillageDayPolicy

    world.state.village.day_policy = VillageDayPolicy(purposeful_downtime=True)
    add(world, "bakery", "bread", 2, expiry=1400)
    order = propose(world, quantity=2)
    accept(world, order)
    carrier = actor(world, "nella", "bakery")
    carrier.state.village.residents["nella"].known_routes = list(
        eid for eid, door in carrier.state.entities.items() if door.kind == "door")
    goods.take(world, "bakery", "bread", 2)
    assert economy.meal_delivery_actions(carrier) == []
    add(world, "bakery", "bread", 2, expiry=1400)
    shipments = carrier.state.village.residents["nella"].shipments
    assert goods.load(carrier, "nella", {"bread": 2}, "tavern", shipments, 3) is None
    assert all(step["action"] != "village_load_goods" for step in economy.meal_delivery_actions(carrier))
    carrier.actor.location_id = "tavern"
    assert goods.unload(carrier, "nella", shipments) is None
    carrier.actor.location_id = "bakery"
    add(world, "bakery", "bread", 2, expiry=1400)
    assert order.status == "fulfilled" and order.loadable == 0
    assert economy.meal_delivery_actions(carrier) == []


def test_capacity_aware_delivery_runs_and_reserve_protection(world):
    add(world, "bakery", "bread", 5, expiry=1400)
    order = propose(world, quantity=5)
    accept(world, order)
    carrier = actor(world, "nella", "bakery")
    runs = economy.delivery_runs(carrier)
    assert runs[0]["goods"] == {"bread": 3}
    assert runs[0]["orders"] == {order.id: 3}
    assert economy.protected_quantity(carrier, "bakery", "bread") == 5


def test_two_services_do_not_reuse_incoming_goods_or_expiring_meals(world):
    economy.configure(world, {**economy.config(world).model_dump(), "meal_plans": [
        {"id": "breakfast", "use_minute": 540, "portions": 4, "recipe_portions": {"bread": 4}},
        {"id": "supper", "use_minute": 1140, "portions": 8, "recipe_portions": {"bread": 8}}]})
    add(world, "bakery", "bread", 2, expiry=1200)
    order = propose(world, quantity=2, deadline=530, fresh=1140)
    accept(world, order)
    requests = economy.suggestions(world)
    assert len(requests) == 1 and requests[0]["quantity"] == 4
    add(world, "tavern", "meals", 4, expiry=600)
    add(world, "tavern", "meals", 8, expiry=1200)
    coverage = economy.meal_requirements(world)
    assert [plan["covered_portions"] for plan in coverage] == [4, 8]
    assert economy.suggestions(world) == []


def test_supplier_stale_for_service_stock_does_not_suppress_fresh_production(world):
    supplier = actor(world, "jory")
    add(world, "bakery", "bread", 3, expiry=1130)
    add(world, "bakery", "flour", 5)
    add(world, "bakery", "bread", 3, expiry=1400)
    order = propose(world)
    accept(world, order)
    goods.take(world, "bakery", "bread", 3, required_fresh_until=1140)
    assert economy.production_blocker(supplier, "village_work") is None
    assert economy.production_batch_limit(supplier, "village_work") == 2


def test_accepted_orders_drive_real_upstream_replenishment(world):
    supplier = actor(world, "jory")
    add(world, "bakery", "flour", 4)
    add(world, "bakery", "bread", 4, expiry=1400)
    order = propose(world, quantity=4)
    accept(world, order)
    requests = economy.suggestions(supplier)
    assert all(request["product"] != "flour" for request in requests)
    goods.take(world, "bakery", "bread", 4)
    goods.take(world, "bakery", "flour", 2)
    flour = next(request for request in economy.suggestions(supplier)
                 if request["product"] == "flour")
    assert flour["supplier"] == "mara" and flour["quantity"] == 2
    assert flour["deadline"] < order.deadline


def test_external_cash_is_ledger_only_and_monitor_shape_is_flat(world):
    before = world.state.village.coins.copy()
    economy.record_external_payment(world, "visiting-merchant", "jory", 7)
    assert world.state.village.coins == before
    world.state.village.coins["jory"] += 7
    add(world, "bakery", "bread", 2, expiry=1400)
    order = propose(world, quantity=2)
    report = economy.monitor(world)
    assert report["orders"][0] == {"id": order.id, "buyer": "elsie", "seller": "jory",
                                  "product": "bread", "quantity": 2, "delivered": 0,
                                  "status": "proposed", "deadline": 1110}
    bread = next(item for item in report["products"] if item["location"] == "bakery" and item["product"] == "bread")
    assert bread == {"location": "bakery", "product": "bread", "quantity": 2, "usable": 2,
                     "expiring": 0, "reserve": 0}
    jory = next(item for item in report["accounts"] if item["owner"] == "jory")
    assert jory["sales"] == jory["net_cash"] == 7
    assert jory["inventory_value"] == 6


def test_already_served_diners_do_not_create_replacement_demand(world):
    world.state.village.minute = 1140
    for rid in list(world.state.village.residents)[:8]:
        world.state.village.residents[rid].life.meals["0:daily"] = VillageMeal(
            kind="daily", due=1140, status="eaten", source="tavern:meals")
    coverage = economy.meal_requirements(world)[0]
    assert coverage["remaining_portions"] == 4 and coverage["eaten_portions"] == 8
    requests = economy.suggestions(world)
    assert all(request["product"] != "bread" for request in requests)
    assert next(request for request in requests if request["product"] == "fish")["quantity"] == 2


def test_delivery_recovery_preserves_exactly_once_cash_and_remaining_quantity(world):
    add(world, "bakery", "bread", 2, expiry=1400)
    order = propose(world, quantity=2)
    accept(world, order)
    carrier = actor(world, "nella", "bakery")
    shipments = carrier.state.village.residents["nella"].shipments
    assert goods.load(carrier, "nella", {"bread": 2}, "tavern", shipments, 3) is None
    goods.sync_cargo(carrier, "nella")
    carrier.save()
    restored = World(world.scenario, world.path, "economy", "nella", "session-nella")
    restored.actor.location_id = "tavern"
    shipments = restored.state.village.residents["nella"].shipments
    assert goods.unload(restored, "nella", shipments) is None
    goods.sync_cargo(restored, "nella")
    restored.save()
    replayed = World(world.scenario, world.path, "economy", "nella", "session-nella")
    coins = replayed.state.village.coins.copy()
    assert goods.unload(replayed, "nella", replayed.state.village.residents["nella"].shipments)
    assert replayed.state.village.coins == coins
    recovered_order = replayed.state.village.economy.orders[order.id]
    assert recovered_order.status == "fulfilled" and recovered_order.paid == 6


@pytest.mark.parametrize("breakfast", [False, True])
def test_example_policy_is_json_valid_complete_and_does_not_mutate_scenario(breakfast):
    value = scenario()
    value.village.breakfast = breakfast
    before = value.model_dump(mode="json")
    example = json.loads(json.dumps(economy.example_config(value)))
    assert value.model_dump(mode="json") == before
    policy = VillageEconomyConfig.model_validate(example)
    value.village.economy = policy
    economy.validate_config(value)
    assert policy.meal_price == 2 and policy.delivery_fee == 1
    assert policy.require_orders and policy.stock_buffer["bread"] == 1
    assert policy.subsidy_payer in value.village.coins and policy.subsidy_per_meal == 1
    assert policy.reserve_targets["forge"]["iron"] == 2
    assert policy.production_targets["forge"]["wagon-parts"] == 1
    assert policy.production_targets["workshop"]["planks"] == 2
    assert policy.suppliers["bread"] == "jory" and policy.suppliers["fish"] == "finn"
    assert len(policy.meal_plans) == (2 if breakfast else 1)
    supper = next(plan for plan in policy.meal_plans if plan.id == "supper")
    assert supper.recipe_portions == {"bread": 8, "fish": 4}
    assert all(sum(plan.recipe_portions.values()) == len(value.village.residents)
               for plan in policy.meal_plans)


@pytest.mark.parametrize("entry", ["village_propose_order", "village_plan_supplies", "_propose"])
def test_remote_order_attempts_are_rejected_without_any_mutation(world, entry):
    args = {"supplier": "jory"}
    if entry != "village_plan_supplies":
        args.update(product="bread", quantity=3, deadline=1110, required_fresh_until=1140)
    before = world.state.model_dump()
    result = economy._propose(world, args) if entry == "_propose" else economy.apply(world, entry, args)
    assert result[:2] == ("REJECTED", "OUT_OF_REACH")
    assert "in person" in result[2]
    assert world.state.model_dump() == before


def test_planning_only_is_pure_and_suppliers_are_ordered_one_at_a_time(world):
    before = world.state.model_dump()
    result = economy.apply(world, "village_plan_supplies", {})
    assert result[:2] == ("APPLIED", "SUPPLIES_PLANNED")
    assert "Jory" in result[2] and "Finn" in result[2] and "No orders were placed" in result[2]
    assert world.state.model_dump() == before
    world.actor.location_id = world.state.actors["jory"].location_id
    assert economy.apply(world, "village_plan_supplies", {"supplier": "jory"})[0] == "APPLIED"
    assert {order.supplier for order in world.state.village.economy.orders.values()} == {"jory"}
    before = world.state.model_dump()
    assert economy.apply(world, "village_plan_supplies", {"supplier": "finn"})[1] == "OUT_OF_REACH"
    assert world.state.model_dump() == before
    world.actor.location_id = world.state.actors["finn"].location_id
    assert economy.apply(world, "village_plan_supplies", {"supplier": "finn"})[0] == "APPLIED"
    assert {order.supplier for order in world.state.village.economy.orders.values()} == {"jory", "finn"}


def test_face_to_face_order_is_spoken_locally_and_supplier_can_respond_later(world):
    actor(world, "jory", "village-square")
    world.actor.location_id = "village-square"
    order = propose(world, quantity=3)
    spoken = next(event for event in world.state.village.events if event.action == "order_proposed")
    assert {"elsie", "jory"} <= set(spoken.witnesses)
    assert "finn" not in spoken.witnesses
    assert "Elsie tells Jory" in spoken.result
    assert "3 bread" in spoken.result and "3 coins each" in spoken.result
    assert "minute 1110" in spoken.result and "minute 1140" in spoken.result
    assert spoken.target == order.id and order.status == "proposed"
    assert spoken in [world.state.village.events[index]
                      for index in world.state.village.residents["jory"].memories]
    world.actor.location_id = "tavern"
    add(world, "bakery", "bread", 3, expiry=1400)
    supplier = actor(world, "jory")
    assert supplier.actor.location_id != world.actor.location_id
    assert economy.apply(supplier, "village_accept_order", {"order_id": order.id})[0] == "APPLIED"
    assert order.status == "accepted"


@pytest.mark.parametrize("busy", ["sleeping", "work", "busy_until", "available_at"])
@pytest.mark.parametrize("entry", ["village_propose_order", "village_plan_supplies"])
def test_unavailable_supplier_cannot_receive_order_and_failed_attempt_is_pure(world, busy, entry):
    world.actor.location_id = world.state.actors["jory"].location_id
    supplier = world.state.village.residents["jory"]
    if busy == "sleeping":
        supplier.life.sleeping = True
    elif busy == "work":
        supplier.life.work = VillageCommittedWork(location="bakery", finishes_at=510, outputs={"bread": 1})
    else:
        setattr(supplier, busy, 510)
    before = world.state.model_dump()
    args = {"supplier": "jory"}
    if entry == "village_propose_order":
        args.update(product="bread", quantity=3, deadline=1110, required_fresh_until=1140)
    result = economy.apply(world, entry, args)
    assert result[1] == ("RESIDENT_ASLEEP" if busy == "sleeping" else "RESIDENT_BUSY")
    assert world.state.model_dump() == before
    descriptor = next(action for action in economy.actions(world) if action["id"] == "village_propose_order")
    assert descriptor["availability"] == "blocked"
    assert ("sleeping" if busy == "sleeping" else "occupied") in descriptor["reason"]


@pytest.mark.parametrize("sleeping", [False, True])
def test_buyer_must_be_awake_and_available_to_speak(world, sleeping):
    world.actor.location_id = world.state.actors["jory"].location_id
    buyer = world.state.village.residents["elsie"]
    if sleeping:
        buyer.life.sleeping = True
    else:
        buyer.available_at = 510
    before = world.state.model_dump()
    assert economy.apply(world, "village_plan_supplies", {"supplier": "jory"})[1] == (
        "ASLEEP" if sleeping else "ACTOR_BUSY")
    assert world.state.model_dump() == before


def test_baker_and_miller_upstream_ordering_cannot_bypass_contact(world):
    add(world, "bakery", "bread", 4, expiry=1400)
    add(world, "bakery", "flour", 4)
    bread = propose(world, quantity=4)
    accept(world, bread)
    baker = actor(world, "jory")
    goods.take(world, "bakery", "bread", 4)
    goods.take(world, "bakery", "flour", 4)
    assert any(request["product"] == "flour" and request["supplier"] == "mara"
               for request in economy.suggestions(baker))
    assert economy.apply(baker, "village_plan_supplies", {})[0] == "APPLIED"
    before = baker.state.model_dump()
    assert economy.apply(baker, "village_plan_supplies", {"supplier": "mara"})[1] == "OUT_OF_REACH"
    assert baker.state.model_dump() == before
    baker.actor.location_id = baker.state.actors["mara"].location_id
    assert economy.apply(baker, "village_plan_supplies", {"supplier": "mara"})[0] == "APPLIED"
    flour = next(order for order in baker.state.village.economy.orders.values()
                 if order.buyer == "jory" and order.product == "flour")
    add(world, "watermill", "flour", flour.quantity)
    accept(world, flour)
    goods.take(world, "watermill", "flour", flour.quantity)
    miller = actor(world, "mara")
    assert any(request["product"] == "grain" and request["supplier"] == "ada"
               for request in economy.suggestions(miller))
    before = miller.state.model_dump()
    assert economy.apply(miller, "village_plan_supplies", {"supplier": "ada"})[1] == "OUT_OF_REACH"
    assert miller.state.model_dump() == before
    miller.actor.location_id = miller.state.actors["ada"].location_id
    assert economy.apply(miller, "village_plan_supplies", {"supplier": "ada"})[0] == "APPLIED"
    assert any(order.buyer == "mara" and order.supplier == "ada" and order.product == "grain"
               for order in miller.state.village.economy.orders.values())


def test_npc_orders_are_participant_only_but_admin_monitor_remains_global(world):
    bread = propose(world, quantity=2)
    fish = propose(world, product="fish", quantity=2, supplier="finn")
    assert {order["id"] for order in economy.observation(world)["orders"]} == {bread.id, fish.id}
    baker = actor(world, "jory")
    assert [order["id"] for order in economy.observation(baker)["orders"]] == [bread.id]
    fisher = actor(world, "finn")
    assert [order["id"] for order in economy.observation(fisher)["orders"]] == [fish.id]
    outsider = actor(world, "torvin")
    report = economy.observation(outsider)
    assert report["orders"] == [] and set(report["cash"]) == {"torvin"}
    assert "tavern" not in report["inventory"]
    assert report["meal_coverage"] == []
    assert {order["id"] for order in economy.monitor(outsider)["orders"]} == {bread.id, fish.id}


def test_contact_guidance_and_action_shapes_are_truthful(world):
    report = economy.observation(world)
    assert all(request["order_availability"] == "blocked" and request["order_code"] == "OUT_OF_REACH"
               for request in report["suggestions"])
    assert "Planning alone places no orders" in report["ordering_guidance"]
    actions = {action["id"]: action for action in economy.actions(world)}
    assert actions["village_plan_supplies"]["arguments_schema"]["required"] == []
    assert "supplier" in actions["village_plan_supplies"]["arguments_schema"]["properties"]
    assert actions["village_propose_order"]["availability"] == "blocked"
    actor(world, "jory", world.actor.location_id)
    actions = {action["id"]: action for action in economy.actions(world)}
    assert actions["village_propose_order"]["availability"] == "available"
    assert actions["village_propose_order"]["arguments_schema"]["properties"]["supplier"]["enum"] == ["jory"]
    bread = next(request for request in economy.observation(world)["suggestions"] if request["product"] == "bread")
    assert bread["order_availability"] == "available" and bread["order_reason"] is None
