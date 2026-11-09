import copy

import pytest

from npc_sim import village, village_economy, village_goods, village_livestock, village_recycling
from npc_sim.models import VillageStockLot
from npc_sim.world import World
from test_village_livestock import make_world, perform, water
from test_world import act


def test_deliberate_safe_scraps_use_surplus_clock_and_original_freshness(tmp_path):
    world = make_world(tmp_path)
    state = world.state.village
    before = state.stocks["farm"]["grain"]
    assert act(world, "village_make_plant_scraps", product="grain", quantity=1)["outcome"] == "APPLIED"
    assert state.stocks["farm"]["grain"] == before - 1
    assert state.stocks["farm"]["plant-scraps"] == 0
    assert act(world, "village_make_plant_scraps", product="grain", quantity=1)["outcome"] == "REJECTED"
    village.advance(world, 5)
    assert state.stocks["farm"]["plant-scraps"] == 1
    lot = state.stock_lots["farm"]["plant-scraps"][0]
    assert lot.ingredients == ["grain"] and lot.expires_at <= lot.produced_at + 720
    assert act(world, "village_make_plant_scraps", product="grain", quantity=2)["code"] == "UNSAFE_OR_COMMITTED_SCRAPS"
    water(world)
    perform(world, "village_care_hens", minutes=10)
    assert state.stocks["farm"]["grain"] == before - 3
    assert state.stocks["farm"]["plant-scraps"] == 0
    assert state.livestock.hens.fed
    world.save()
    loaded = World(world.scenario, world.path, "livestock", "ada", "session-ada")
    assert loaded.state.village.stock_lots == state.stock_lots


@pytest.mark.parametrize("resource", ["fish", "milk", "eggs", "meals", "compost-material"])
def test_animal_unknown_meal_or_rotten_material_is_never_chicken_scraps(tmp_path, resource):
    world = make_world(tmp_path)
    village_goods.put(world, "farm", resource, 2)
    before = copy.deepcopy(world.state.village.stocks["farm"])
    result = act(world, "village_make_plant_scraps", product=resource, quantity=1)
    assert result["code"] == "UNSAFE_OR_COMMITTED_SCRAPS"
    assert world.state.village.stocks["farm"] == before


def test_spoilage_recovers_finite_food_material_and_not_feed_at_original_location(tmp_path):
    world = make_world(tmp_path)
    state = world.state.village
    for resource in ["milk", "eggs", "fish", "bread"]:
        village_goods.put(world, "tavern", resource, 1, [
            VillageStockLot(quantity=1, produced_at=590, expires_at=605)])
    village.advance(world, 5)
    assert state.stocks["tavern"]["compost-material"] == 4
    assert state.stocks["tavern"]["plant-scraps"] == 0
    assert all(lot.source_resource in {"milk", "eggs", "fish", "bread"}
               for lot in state.stock_lots["tavern"]["compost-material"])
    before = copy.deepcopy(state.stocks)
    village_goods.advance(world)
    village_goods.advance(world)
    assert state.stocks == before
    assert state.stocks["farm"]["compost-material"] == 0
    inn = world.bind("elsie", "session-elsie")
    inn.actor.location_id = "tavern"
    assert act(inn, "village_make_plant_scraps", product="milk", quantity=1)["code"] == "UNSAFE_OR_COMMITTED_SCRAPS"


def test_scraps_cannot_refresh_old_food_or_feed_after_expiry_and_compost_is_timed(tmp_path):
    world = make_world(tmp_path)
    state = world.state.village
    village_goods.put(world, "farm", "bread", 2, [
        VillageStockLot(quantity=2, produced_at=590, expires_at=620, ingredients=["flour"])])
    perform(world, "village_make_plant_scraps", product="bread", quantity=2, minutes=10)
    lot = state.stock_lots["farm"]["plant-scraps"][0]
    assert lot.expires_at == 620
    village.advance(world, 10)
    assert state.stocks["farm"]["plant-scraps"] == 0
    assert state.stocks["farm"]["compost-material"] == 2
    assert village_recycling.hen_ration(world) == (3, 0)
    assert act(world, "village_compost", material="compost-material")["outcome"] == "APPLIED"
    assert state.stocks["farm"]["compost-material"] == 0
    assert state.stocks["farm"]["compost"] == 0
    assert act(world, "village_compost", material="compost-material")["outcome"] == "REJECTED"
    village.advance(world, 15)
    assert state.stocks["farm"]["compost"] == 0
    village.advance(world, 5)
    assert state.stocks["farm"]["compost"] == 1
    manure = state.stocks["farm"]["manure"]
    perform(world, "village_fertilize_compost")
    assert state.stocks["farm"]["compost"] == 0 and state.stocks["farm"]["manure"] == manure
    assert state.residents["ada"].fertilized_harvests == 3
    assert act(world, "village_compost", material="compost-material")["code"] == "COMPOST_MATERIAL_REQUIRED"


def test_compost_material_and_scraps_trade_uses_real_orders_cargo_and_coins(tmp_path):
    world = make_world(tmp_path)
    state = world.state.village
    inn = world.bind("elsie", "session-elsie")
    village_goods.put(world, "tavern", "bread", 30)
    perform(inn, "village_make_plant_scraps", product="bread", quantity=1)
    village_goods.put(world, "tavern", "milk", 2, [
        VillageStockLot(quantity=2, produced_at=590, expires_at=state.minute + 5)])
    village.advance(world, 5)
    assert state.stocks["tavern"]["compost-material"] == 2
    suggestions = village_economy.suggestions(world)
    assert any(item["product"] == "compost-material" and item["supplier"] == "elsie" for item in suggestions)
    assert any(item["product"] == "plant-scraps" and item["supplier"] == "elsie" for item in suggestions)
    world.actor.location_id = "tavern"
    before = copy.deepcopy(state.coins)
    for product, quantity in [("plant-scraps", 1), ("compost-material", 2)]:
        perform(world, "village_propose_order", supplier="elsie", product=product, quantity=quantity,
                deadline=900, required_fresh_until=950)
        order = list(state.economy.orders.values())[-1]
        perform(inn, "village_accept_order", order_id=order.id)
    courier = world.bind("nella", "session-nella")
    courier.actor.location_id = "tavern"
    perform(courier, "village_load_goods", goods={"plant-scraps": 1, "compost-material": 2}, destination="farm")
    assert state.stocks["farm"]["compost-material"] == 0 and state.coins == before
    courier.actor.location_id = "farm"
    perform(courier, "village_unload_goods")
    assert state.stocks["farm"]["plant-scraps"] == 1 and state.stocks["farm"]["compost-material"] == 2
    assert sum(state.coins.values()) == sum(before.values())
    assert state.coins["elsie"] == before["elsie"] + 3
    world.actor.location_id = "farm"
    perform(world, "village_compost", material="compost-material", minutes=20)
    assert state.stocks["farm"]["compost"] == 1


def test_missing_tools_interrupted_compost_and_legacy_spoilage_keep_conservation(tmp_path):
    world = make_world(tmp_path)
    state = world.state.village
    village_goods.put(world, "farm", "milk", 4, [
        VillageStockLot(quantity=4, produced_at=590, expires_at=605)])
    village.advance(world, 5)
    world.state.entities["compost-fork"].health = 0
    assert act(world, "village_compost", material="compost-material")["code"] == "LIVESTOCK_TOOL_REQUIRED"
    world.state.entities["compost-fork"].health = 100
    act(world, "village_compost", material="compost-material")
    village.advance(world, 5)
    act(world, "village_livestock_pause")
    assert state.stocks["farm"]["compost"] == 0 and state.stocks["farm"]["compost-material"] == 2
    village.advance(world, 5)
    perform(world, "village_compost", material="compost-material", minutes=20)
    assert state.stocks["farm"]["compost"] == 1 and state.stocks["farm"]["compost-material"] == 0
    old = make_world(tmp_path / "legacy", enabled=False)
    village_goods.put(old, "farm", "bread", 1, [
        VillageStockLot(quantity=1, produced_at=590, expires_at=605)])
    village.advance(old, 5)
    assert old.state.village.stocks["farm"].get("compost-material", 0) == 0


def test_expired_cargo_remains_real_material_at_actual_site_and_can_be_ordered_and_hauled(tmp_path):
    from npc_sim.models import VillageShipment
    world = make_world(tmp_path)
    state = world.state.village
    courier = world.bind("nella", "session-nella")
    courier.actor.location_id = "cottages"
    state.residents["nella"].shipments.append(VillageShipment(
        resource="milk", quantity=2, seller="ada", buyer="elsie", destination="tavern", price=2,
        lots=[VillageStockLot(quantity=2, produced_at=590, expires_at=605)]))
    village.advance(world, 5)
    assert not state.residents["nella"].shipments
    assert state.stocks["cottages"]["compost-material"] == 2
    village_goods.advance(world)
    assert state.stocks["cottages"]["compost-material"] == 2
    world.actor.location_id = "cottages"
    perform(world, "village_propose_order", supplier="nella", product="compost-material", quantity=2,
            deadline=900, required_fresh_until=950)
    order = list(state.economy.orders.values())[-1]
    assert order.source == "cottages" and order.destination == "farm"
    perform(courier, "village_accept_order", order_id=order.id)
    before = sum(state.coins.values())
    perform(courier, "village_load_goods", goods={"compost-material": 2}, destination="farm")
    assert state.stocks["cottages"]["compost-material"] == 0
    courier.actor.location_id = "farm"
    perform(courier, "village_unload_goods")
    assert state.stocks["farm"]["compost-material"] == 2 and order.status == "fulfilled"
    assert sum(state.coins.values()) == before


def test_native_meal_and_baking_inputs_are_not_scrap_surplus(tmp_path):
    world = make_world(tmp_path)
    state = world.state.village
    inn = world.bind("elsie", "session-elsie")
    for resource in ["bread", "meals"]:
        quantity = state.stocks["tavern"].get(resource, 0)
        if quantity:
            village_goods.take(world, "tavern", resource, quantity)
    village_goods.put(world, "tavern", "bread", 1)
    before = state.stocks["tavern"]["bread"]
    assert act(inn, "village_make_plant_scraps", product="bread", quantity=1)["code"] == "UNSAFE_OR_COMMITTED_SCRAPS"
    assert state.stocks["tavern"]["bread"] == before
    baker = world.bind("jory", "session-jory")
    village_goods.put(world, "bakery", "flour", 6)
    state.economy.config.production_targets.setdefault("bakery", {})["bread"] = 100
    before = state.stocks["bakery"]["flour"]
    assert act(baker, "village_make_plant_scraps", product="flour", quantity=1)["code"] == "UNSAFE_OR_COMMITTED_SCRAPS"
    assert state.stocks["bakery"]["flour"] == before


def test_only_proven_plant_meals_can_be_scraps_and_recycling_preserves_explicit_lot_provenance(tmp_path):
    world = make_world(tmp_path)
    state = world.state.village
    village_goods.put(world, "farm", "meals", 1, ingredients=["fish"])
    assert act(world, "village_make_plant_scraps", product="meals", quantity=1)["code"] == "UNSAFE_OR_COMMITTED_SCRAPS"
    village_goods.take(world, "farm", "meals", 1)
    village_goods.put(world, "farm", "meals", 1, ingredients=["bread"])
    perform(world, "village_make_plant_scraps", product="meals", quantity=1)
    lot = state.stock_lots["farm"]["plant-scraps"][0]
    assert lot.ingredients == ["bread"] and lot.source_resource == "meals"
    world.save()
    loaded = World(world.scenario, world.path, "livestock", "ada", "session-ada")
    assert loaded.state.village.stock_lots["farm"]["plant-scraps"][0] == lot


def test_legacy_stock_lots_and_committed_batches_keep_exact_serialized_shape():
    from npc_sim.models import VillageCommittedWork
    raw = {"quantity": 2, "produced_at": 600, "expires_at": None}
    assert VillageStockLot.model_validate(raw).model_dump() == raw
    work = {"location": "bakery", "finishes_at": 700, "outputs": {"bread": 2}, "equipment": None, "kind": "production"}
    assert VillageCommittedWork.model_validate(work).model_dump() == work
