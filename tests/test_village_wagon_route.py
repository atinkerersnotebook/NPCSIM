from npc_sim import village, village_goods, village_services, village_wagon
from npc_sim.models import VillageStockLot
from npc_sim.village_economy_models import VillageSupplierOrder
from test_village_economy import actor, world  # noqa: F401
from test_world import act


def complete_route(world, wagon, limit=80):
    for _ in range(limit):
        if not wagon.automatic:
            break
        world.state.village.minute = wagon.next_event_at
        village_wagon.advance(world)


def test_ready_orders_for_same_route_are_loaded_as_one_batch(world):
    courier = actor(world, "nella")
    state = courier.state.village
    wagon = state.wagon
    wagon.repaired = wagon.hitched = True
    courier.actor.location_id = courier.state.entities["wagon"].location
    for identifier, product, quantity in (("bread", "bread", 4), ("bread-extra", "bread", 2)):
        state.economy.orders[identifier] = VillageSupplierOrder(
            id=identifier, request_key=identifier, buyer="elsie", supplier="jory",
            source="bakery", destination="tavern", product=product, quantity=quantity,
            price=1, deadline=900, required_fresh_until=850, created_at=state.minute,
            status="ready_to_collect")
    village_goods.put(courier, "bakery", "bread", 6,
                      [VillageStockLot(quantity=6, produced_at=state.minute, expires_at=1000)])

    assert village_wagon.apply(courier, "village_start_route", {})[:2] == ("APPLIED", "ROUTE_STARTED")
    complete_route(courier, wagon)

    assert sum(order.delivered_quantity for order in state.economy.orders.values()) == 6
    assert not wagon.loads and wagon.driver is None


def test_broken_wagon_blocks_delivery_until_real_repair(world):
    smith = actor(world, "torvin", "forge")
    courier = actor(world, "nella", "forge")
    state = courier.state.village
    wagon = state.wagon
    wagon.repaired = wagon.hitched = True
    wagon_entity = courier.state.entities["wagon"]
    wagon_entity.health, wagon_entity.max_health = 0, 100
    assert village_wagon.readiness(courier)["blocker"] == "WAGON_REPAIR_REQUIRED"
    assert village_wagon.apply(courier, "village_start_route", {})[0] == "REJECTED"

    village_goods.put(smith, "forge", "wagon-parts", 1)
    assert village_wagon.apply(smith, "village_fit_wagon", {})[:2] == ("APPLIED", "WAGON_REPAIR_STARTED")
    state.minute += 30
    from npc_sim import village_life
    village_life.advance(smith)

    assert wagon.repaired and not wagon_entity.broken
    assert wagon_entity.health == wagon_entity.max_health == 100


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
    assert village_wagon.readiness(courier)["blocker"] == "NO_CONFIRMED_SHIPMENT"
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
    assert village_wagon.readiness(courier)["shipments"][0]["order_id"] == order.id
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
    assert village_wagon.readiness(courier)["blocker"] == "NO_CONFIRMED_SHIPMENT"


def test_unfunded_order_is_not_wagon_ready(world):
    courier = actor(world, "nella")
    state = courier.state.village
    state.wagon.repaired = state.wagon.hitched = True
    courier.actor.location_id = courier.state.entities["wagon"].location
    state.economy.orders["unfunded"] = VillageSupplierOrder(
        id="unfunded", request_key="unfunded", buyer="elsie", supplier="jory",
        source="bakery", destination="tavern", product="bread", quantity=2, price=1,
        deadline=900, required_fresh_until=850, created_at=state.minute, status="ready_to_collect")
    village_goods.put(courier, "bakery", "bread", 2,
                      [VillageStockLot(quantity=2, produced_at=state.minute, expires_at=1000)])
    state.coins["elsie"] = 0
    assert village_wagon.readiness(courier)["shipments"] == []
    assert village_wagon.apply(courier, "village_start_route", {})[0] == "REJECTED"


def test_order_too_late_for_physical_wagon_route_is_not_ready(world):
    courier = actor(world, "nella")
    state = courier.state.village
    state.wagon.repaired = state.wagon.hitched = True
    courier.actor.location_id = courier.state.entities["wagon"].location
    state.economy.orders["too-late"] = VillageSupplierOrder(
        id="too-late", request_key="too-late", buyer="elsie", supplier="jory",
        source="bakery", destination="tavern", product="bread", quantity=1, price=1,
        deadline=state.minute + 5, required_fresh_until=850, created_at=state.minute,
        status="ready_to_collect")
    village_goods.put(courier, "bakery", "bread", 1,
                      [VillageStockLot(quantity=1, produced_at=state.minute, expires_at=1000)])
    assert village_wagon.readiness(courier)["blocker"] == "NO_CONFIRMED_SHIPMENT"
    assert village_wagon.apply(courier, "village_start_route", {})[0] == "REJECTED"


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


def test_funded_farm_to_mill_wagon_delivery_survives_reload_and_settles_once(world):
    courier = actor(world, "nella")
    state = courier.state.village
    wagon = state.wagon
    wagon.repaired = wagon.hitched = True
    courier.actor.location_id = courier.state.entities["wagon"].location
    order = VillageSupplierOrder(
        id="farm-mill", request_key="farm-mill", buyer="mara", supplier="ada",
        source="farm", destination="watermill", product="grain", quantity=3, price=1,
        deadline=900, required_fresh_until=850, created_at=state.minute,
        status="ready_to_collect")
    state.economy.orders[order.id] = order
    village_goods.put(courier, "farm", "grain", 3,
                      [VillageStockLot(quantity=3, produced_at=state.minute, expires_at=1000)])
    balances = state.coins.copy()
    assert village_wagon.apply(courier, "village_start_route", {})[:2] == ("APPLIED", "ROUTE_STARTED")
    for _ in range(60):
        if not wagon.automatic:
            break
        state.minute = wagon.next_event_at
        village_wagon.advance(courier)
    assert not wagon.automatic and wagon.driver is None and not wagon.loads
    assert order.status == "fulfilled" and order.delivered_quantity == 3
    assert state.stocks["watermill"]["grain"] == 3
    assert state.coins["mara"] == balances["mara"] - order.paid
    assert state.coins["ada"] == balances["ada"] + order.paid
    assert courier.state.entities["wagon"].location == "forge"
    assert courier.state.entities["work-horse"].location == "forge"
    courier.save()
    from npc_sim.world import World
    restored = World(courier.scenario, courier.path, courier.state.world_id, "nella", "session-nella")
    assert restored.state.village.economy.orders[order.id].delivered_quantity == 3
    assert village_wagon.apply(restored, "village_unload_wagon", {})[0] == "REJECTED"
    assert restored.state.village.coins == state.coins
    handler = actor(restored, "bren", "forge")
    state = restored.state.village
    assert village_services.apply(handler, "village_unhitch", {})[:2] == ("APPLIED", "HORSE_UNHITCHED")
    path = village.route(handler, "forge", "stable")
    assert path is not None
    for location in path:
        door = next(eid for eid, entity in handler.state.entities.items()
                    if entity.kind == "door" and {entity.location, entity.destination}
                    == {handler.actor.location_id, location})
        state.minute = max(state.minute, state.residents["bren"].available_at)
        result = act(handler, "go", target=door)
        assert result["outcome"] == "APPLIED", result
        assert handler.state.entities["work-horse"].location == location
    state.minute = max(state.minute, state.residents["bren"].available_at)
    assert village_services.apply(handler, "village_release_horse", {})[:2] == ("APPLIED", "HORSE_STABLED")
    assert not restored.state.village.wagon.hitched and restored.state.village.horse_leader is None


def test_route_goes_to_funded_pickup_without_empty_circuit_and_resumes_loaded_cargo(world):
    courier = actor(world, "nella")
    state = courier.state.village
    wagon = state.wagon
    wagon.repaired = wagon.hitched = True
    courier.actor.location_id = courier.state.entities["wagon"].location
    order = VillageSupplierOrder(
        id="bakery-pickup", request_key="bakery-pickup", buyer="elsie", supplier="jory",
        source="bakery", destination="tavern", product="bread", quantity=2, price=1,
        deadline=900, required_fresh_until=850, created_at=state.minute,
        status="ready_to_collect")
    state.economy.orders[order.id] = order
    village_goods.put(courier, "bakery", "bread", 2,
                      [VillageStockLot(quantity=2, produced_at=state.minute, expires_at=1000)])
    origin = courier.actor.location_id
    assert village_wagon.apply(courier, "village_start_route", {})[0] == "APPLIED"
    state.minute = wagon.next_event_at
    village_wagon.advance(courier)
    assert courier.actor.location_id == village.route(courier, origin, "bakery", road_only=True)[0]
    assert courier.state.entities["work-horse"].location == courier.actor.location_id
    for _ in range(20):
        if wagon.loads:
            break
        state.minute = wagon.next_event_at
        village_wagon.advance(courier)
    assert len(wagon.loads) == 1 and order.in_transit == 2
    assert state.stocks["bakery"]["bread"] == 0
    courier.save()
    from npc_sim.world import World
    restored = World(courier.scenario, courier.path, courier.state.world_id, "nella", "session-nella")
    state = restored.state.village
    wagon = state.wagon
    for _ in range(30):
        if not wagon.automatic:
            break
        state.minute = wagon.next_event_at
        village_wagon.advance(restored)
    assert not wagon.automatic and not wagon.loads
    assert state.economy.orders[order.id].delivered_quantity == 2
    assert state.stocks["tavern"]["bread"] == 2
    coins = state.coins.copy()
    assert village_wagon.apply(restored, "village_start_route", {})[0] == "REJECTED"
    assert village_wagon.apply(restored, "village_unload_wagon", {})[0] == "REJECTED"
    assert state.coins == coins
