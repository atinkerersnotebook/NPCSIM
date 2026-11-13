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
