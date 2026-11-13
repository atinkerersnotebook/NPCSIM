from npc_sim import village_goods, village_wagon
from npc_sim.models import VillageStockLot
from npc_sim.village_economy_models import VillageSupplierOrder
from test_village_economy import actor, world  # noqa: F401


def test_standing_route_requires_real_confirmed_shipment(world):
    courier = actor(world, "nella")
    state = courier.state.village
    wagon = state.wagon
    wagon.repaired = True
    wagon.hitched = True
    courier.actor.location_id = courier.state.entities["wagon"].location

    def start():
        return village_wagon.apply(courier, "village_start_route", {})

    assert start()[0] == "REJECTED"
    assert not wagon.automatic and wagon.driver is None

    order = VillageSupplierOrder(
        id="wagon-order", request_key="wagon-order", buyer="elsie", supplier="jory",
        source="bakery", destination="tavern", product="bread", quantity=2, price=1,
        deadline=900, required_fresh_until=850, created_at=state.minute, status="proposed",
    )
    state.economy.orders[order.id] = order
    village_goods.put(courier, "bakery", "bread", 2,
                      [VillageStockLot(quantity=2, produced_at=state.minute, expires_at=1000)])
    assert start()[0] == "REJECTED"
    order.status = "ready_to_collect"
    assert start()[:2] == ("APPLIED", "ROUTE_STARTED")
    assert wagon.automatic and wagon.driver == "nella"


def test_confirmed_order_is_carried_and_route_returns_for_horse(world):
    courier = actor(world, "nella")
    state = courier.state.village
    wagon = state.wagon
    wagon.repaired = wagon.hitched = True
    courier.actor.location_id = courier.state.entities["wagon"].location
    state.economy.orders["bread-run"] = VillageSupplierOrder(
        id="bread-run", request_key="bread-run", buyer="elsie", supplier="jory",
        source="bakery", destination="tavern", product="bread", quantity=2, price=1,
        deadline=900, required_fresh_until=850, created_at=state.minute,
        status="ready_to_collect",
    )
    village_goods.put(courier, "bakery", "bread", 2,
                      [VillageStockLot(quantity=2, produced_at=state.minute, expires_at=1000)])
    assert village_wagon.apply(courier, "village_start_route", {})[:2] == ("APPLIED", "ROUTE_STARTED")
    for _ in range(60):
        if not wagon.automatic:
            break
        state.minute = wagon.next_event_at
        village_wagon.advance(courier)
    assert not wagon.automatic
    assert wagon.driver is None
    assert state.economy.orders["bread-run"].delivered_quantity == 2
    assert wagon.loads == []
    assert courier.state.entities["wagon"].location == "forge"
    assert courier.state.entities["work-horse"].location == "forge"
    assert courier.actor.location_id == "forge"
    assert state.stocks["tavern"]["bread"] == 2


def test_route_with_cargo_delivers_even_without_new_pickup(world):
    courier = actor(world, "nella")
    state = courier.state.village
    wagon = state.wagon
    wagon.repaired = wagon.hitched = True
    courier.actor.location_id = courier.state.entities["wagon"].location
    order = VillageSupplierOrder(
        id="already-loaded", request_key="already-loaded", buyer="elsie", supplier="jory",
        source="bakery", destination="tavern", product="bread", quantity=1, price=1,
        deadline=900, required_fresh_until=850, created_at=state.minute,
        status="ready_to_collect",
    )
    state.economy.orders[order.id] = order
    village_goods.put(courier, "bakery", "bread", 1,
                      [VillageStockLot(quantity=1, produced_at=state.minute, expires_at=1000)])
    courier.actor.location_id = "bakery"
    assert village_goods.load(courier, "nella", {"bread": 1}, "tavern", wagon.loads, 6) is None
    assert order.in_transit == 1 and order.loadable == 0
    courier.actor.location_id = courier.state.entities["wagon"].location
    assert village_wagon.apply(courier, "village_start_route", {})[:2] == ("APPLIED", "ROUTE_STARTED")
    for _ in range(60):
        if not wagon.automatic:
            break
        state.minute = wagon.next_event_at
        village_wagon.advance(courier)
    assert not wagon.automatic and not wagon.loads
    assert courier.state.entities["wagon"].location == "forge"
    assert order.delivered_quantity == 1
    assert state.stocks["tavern"]["bread"] == 1


def test_route_uses_order_destination_not_legacy_delivery(world):
    courier = actor(world, "nella")
    state = courier.state.village
    wagon = state.wagon
    wagon.repaired = wagon.hitched = True
    courier.actor.location_id = courier.state.entities["wagon"].location
    order = VillageSupplierOrder(
        id="grain-run", request_key="grain-run", buyer="jory", supplier="ada",
        source="farm", destination="bakery", product="grain", quantity=1, price=1,
        deadline=900, required_fresh_until=850, created_at=state.minute,
        status="ready_to_collect",
    )
    state.economy.orders[order.id] = order
    village_goods.put(courier, "farm", "grain", 1,
                      [VillageStockLot(quantity=1, produced_at=state.minute, expires_at=1000)])
    assert village_wagon.apply(courier, "village_start_route", {})[:2] == ("APPLIED", "ROUTE_STARTED")
    for _ in range(60):
        if not wagon.automatic:
            break
        state.minute = wagon.next_event_at
        village_wagon.advance(courier)
    assert not wagon.automatic
    assert order.delivered_quantity == 1
    assert state.stocks["bakery"]["grain"] == 1
    assert state.stocks["watermill"].get("grain", 0) == 0
