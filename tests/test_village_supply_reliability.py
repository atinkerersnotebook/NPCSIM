"""Firm supply promises must agree with physical loading and settlement."""

import pytest

from npc_sim import village_economy as economy, village_goods as goods, village_services
from npc_sim.models import VillageCommittedWork, VillageStockLot
from npc_sim.village_economy_models import VillageEconomyConfig
from npc_sim.world import World
from test_village_expanded import scenario


@pytest.fixture
def world(tmp_path):
    value = scenario()
    value.village.economy = VillageEconomyConfig(reserve_targets={"farm": {"grain": 4},
                                                                "bakery": {"bread": 1}})
    value.village.start_minute = 480
    value.village.goal_deadlines = {}
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
    result = World(value, tmp_path / "supply.json", "supply", "mara", "session-mara")
    for agent in value.agents:
        result.bind(agent.id, f"session-{agent.id}")
    for stock in result.state.village.stocks.values():
        for product in stock:
            stock[product] = 0
    result.state.village.stock_lots = {}
    result.state.village.coins["mara"] = 100
    result.state.village.coins["elsie"] = 100
    return result


def actor(world, rid, location=None):
    bound = world.bind(rid, f"session-{rid}")
    if location:
        bound.actor.location_id = location
    return bound


def add(world, location, product, amount):
    goods.put(world, location, product, amount,
              [VillageStockLot(quantity=amount, produced_at=480, expires_at=1500)], kind="imported")


def order(world, buyer, supplier, product, quantity, deadline=900, fresh=960):
    bound = actor(world, buyer, world.scenario.village.residents[supplier].work)
    result = economy.apply(bound, "village_propose_order", {
        "supplier": supplier, "product": product, "quantity": quantity,
        "deadline": deadline, "required_fresh_until": fresh,
        "request_key": f"{buyer}:{supplier}:{product}:{len(world.state.village.economy.orders)}"})
    assert result[0] == "APPLIED", result
    return next(reversed(world.state.village.economy.orders.values()))


def accept(world, entry):
    return economy.apply(actor(world, entry.supplier), "village_accept_order", {"order_id": entry.id})


def test_t09_protected_grain_cannot_be_firmly_accepted(world):
    add(world, "farm", "grain", 4)
    entry = order(world, "mara", "ada", "grain", 1, deadline=510, fresh=540)
    result = accept(world, entry)
    assert result[:2] == ("REJECTED", "SUPPLY_NOT_READY")
    assert entry.status == "deferred" and "production slot" in result[2]
    assert economy.delivery_runs(actor(world, "nella", "farm")) == []


def test_t10_firm_grain_loads_without_using_reserve(world):
    add(world, "farm", "grain", 5)
    entry = order(world, "mara", "ada", "grain", 1)
    assert accept(world, entry)[:2] == ("APPLIED", "ORDER_ACCEPTED")
    carrier = actor(world, "nella", "farm")
    assert economy.delivery_runs(carrier)[0]["orders"] == {entry.id: 1}
    assert village_services.foot_load(carrier, "nella", {"grain": 1}, "watermill") is None
    assert carrier.state.village.stocks["farm"]["grain"] == 4
    assert entry.in_transit == 1


def test_t11_bread_shortfall_has_early_explicit_response(world):
    add(world, "bakery", "bread", 2)
    entry = order(world, "elsie", "jory", "bread", 4, deadline=570, fresh=600)
    result = accept(world, entry)
    assert result[:2] == ("REJECTED", "SUPPLY_NOT_READY")
    assert "production slot" in result[2]
    assert entry.status != "accepted"


def schedule_bread(world, quantity):
    apprentice = world.scenario.village.residents["hobb"]
    apprentice.job = world.scenario.village.residents["jory"].job.model_copy(deep=True)
    apprentice.job.location = "bakery"
    world.state.village.residents["hobb"].life.work = VillageCommittedWork(
        location="bakery", finishes_at=1050, outputs={"bread": quantity})


def test_scheduled_bread_is_firm_only_when_physically_loadable(world):
    add(world, "bakery", "bread", 1)
    add(world, "bakery", "flour", 4)
    entry = order(world, "elsie", "jory", "bread", 4, deadline=1110, fresh=1140)
    assert accept(world, entry)[:2] == ("APPLIED", "ORDER_ACCEPTED_PLANNED")
    assert entry.production_plan["input:flour"] == 4
    schedule_bread(world, 4)
    assert economy.loadable_for_order(world, entry) == 0
    add(world, "bakery", "bread", 4)
    assert economy.loadable_for_order(world, entry) == 4


def test_t14_scheduled_batch_cannot_back_two_firm_orders(world):
    schedule_bread(world, 2)
    first = order(world, "elsie", "jory", "bread", 2, deadline=1110, fresh=1140)
    assert accept(world, first)[:2] == ("APPLIED", "ORDER_ACCEPTED")
    second = order(world, "mara", "jory", "bread", 1, deadline=1110, fresh=1140)
    assert accept(world, second)[:2] == ("REJECTED", "SUPPLY_NOT_READY")
    assert economy.loadable_for_order(world, first) == 0
    add(world, "bakery", "bread", 1)
    assert economy.loadable_for_order(world, first) == 0
    assert accept(world, second)[:2] == ("REJECTED", "SUPPLY_NOT_READY")
    add(world, "bakery", "bread", 2)
    assert economy.loadable_for_order(world, first) == 2
    assert accept(world, second)[:2] == ("APPLIED", "ORDER_ACCEPTED")


def test_t12_partial_delivery_reconciles_outstanding_and_payment(world):
    add(world, "bakery", "bread", 3)
    entry = order(world, "elsie", "jory", "bread", 2)
    assert accept(world, entry)[0] == "APPLIED"
    carrier = actor(world, "nella", "bakery")
    before = len(world.state.village.economy.payments)
    assert village_services.foot_load(carrier, "nella", {"bread": 1}, "tavern") is None
    actor(world, "nella", "tavern")
    assert village_services.foot_unload(carrier, "nella") is None
    assert (entry.delivered_quantity, entry.remaining, entry.in_transit) == (1, 1, 0)
    assert entry.paid == entry.price and len(world.state.village.economy.payments) > before


def test_t14_two_buyers_cannot_allocate_same_surplus(world):
    add(world, "farm", "grain", 5)
    first = order(world, "mara", "ada", "grain", 1)
    assert accept(world, first)[0] == "APPLIED"
    second = order(world, "elsie", "ada", "grain", 1, deadline=510, fresh=540)
    assert accept(world, second)[:2] == ("REJECTED", "SUPPLY_NOT_READY")
    assert economy.loadable_for_order(world, first) == 1
    assert second.status == "deferred"
