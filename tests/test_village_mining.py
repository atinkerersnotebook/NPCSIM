import copy

import pytest

from npc_sim import village, village_economy, village_extensions, village_goods, village_mining, village_services
from npc_sim.models import VillageEconomyConfig
from npc_sim.world import World
from test_village_expanded import scenario
from test_world import act


def make_world(tmp_path, mining=True):
    value = scenario()
    value.village.start_minute = 600
    policy = village_economy.example_config(value)
    value.village.economy = VillageEconomyConfig.model_validate(policy)
    value.village.stocks["forge"] = {"iron": 0, "wagon-parts": 7, "planks": 2, "logs": 2}
    if mining:
        village_mining.install_scenario(value)
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
    world = World(value, tmp_path / "mining.json", "mining", "torvin", "session-torvin")
    for agent in value.agents:
        world.bind(agent.id, f"session-{agent.id}")
    return world


def perform(world, action, minutes=5, **args):
    result = act(world, action, **args)
    assert result["outcome"] == "APPLIED", (result["code"], result["explanation"])
    village.advance(world, minutes)
    return result


def test_daily_imports_create_real_funded_in_person_orders_and_delivery(tmp_path):
    world = make_world(tmp_path, mining=False)
    world.actor.location_id = "village-square"
    assert act(world, "village_buy_iron", quantity=1)["code"] == "TRADER_NOT_PRESENT"
    village.advance(world, 120)
    assert world.state.village.stocks["village-square"]["iron"] == 4
    before = copy.deepcopy(world.state.village.coins)
    perform(world, "village_buy_iron", quantity=2)
    order = next(iter(world.state.village.economy.orders.values()))
    assert order.supplier == "iron-trader" and order.status == "accepted" and order.in_transit == 2
    assert world.state.village.stocks["forge"]["iron"] == 0
    assert world.state.village.coins == before
    perform(world, "go", target="square-forge")
    perform(world, "village_unload_goods")
    assert world.state.village.stocks["forge"]["iron"] == 2 and order.status == "fulfilled"
    assert world.state.village.coins["torvin"] == before["torvin"] - 6
    assert world.state.village.coins["iron-trader"] == before["iron-trader"] + 6
    assert not world.state.village.residents["torvin"].shipments
    assert sum(world.state.village.coins.values()) == sum(before.values())
    village.advance(world, 1080 - world.state.village.minute)
    assert world.state.village.stocks["village-square"]["iron"] == 0
    village.advance(world, 1440 + 720 - world.state.village.minute)
    assert world.state.village.stocks["village-square"]["iron"] == 4


def test_imports_preserve_funds_stock_capacity_and_buyer_authority(tmp_path):
    world = make_world(tmp_path)
    village.advance(world, 120)
    world.actor.location_id = "village-square"
    world.state.village.coins["torvin"] = 0
    assert act(world, "village_buy_iron", quantity=1)["code"] == "INSUFFICIENT_COINS"
    assert not world.state.village.economy.orders and world.state.village.stocks["village-square"]["iron"] == 4
    courier = world.bind("nella", "session-nella")
    courier.actor.location_id = "village-square"
    assert act(courier, "village_buy_iron", quantity=1)["code"] == "BUYER_MUST_ORDER"
    assert village_services.purchase_iron(world, True)[1] == "IRON_CARRYING_CAPACITY"
    world.state.village.coins["torvin"] = 30
    revision = world.revision()
    result = world.act("purchase-once", revision, "village_buy_iron", {"quantity": 1})
    assert world.act("purchase-once", revision, "village_buy_iron", {"quantity": 1}) == result
    assert world.state.village.stocks["village-square"]["iron"] == 3 and len(world.state.village.economy.orders) == 1


def test_mining_hauling_and_smelting_use_real_locations_time_fuel_and_no_money(tmp_path):
    world = make_world(tmp_path)
    world.state.village.coins["torvin"] = 0
    before = copy.deepcopy(world.state.village.coins)
    assert act(world, "village_mine_ore")["code"] == "MINING_NOT_PRESENT"
    perform(world, "go", target="square-forge")
    perform(world, "go", target="square-mine")
    assert act(world, "village_mine_ore")["code"] == "MINING_STARTED"
    assert world.state.village.stocks["iron-mine"]["iron-ore"] == 0
    assert act(world, "go", target="mine-square")["code"] == "ACTOR_BUSY"
    village.advance(world, 30)
    assert world.state.village.stocks["iron-mine"]["iron-ore"] == 1
    perform(world, "village_mine_ore", minutes=30)
    assert act(world, "village_mine_ore")["code"] == "DEMAND_MET"
    perform(world, "village_load_ore", quantity=2)
    assert world.state.village.stocks["iron-mine"]["iron-ore"] == 0
    assert world.state.village.stocks["forge"].get("iron-ore", 0) == 0
    perform(world, "go", target="mine-square")
    perform(world, "go", target="square-forge")
    perform(world, "village_unload_goods")
    assert world.state.village.coins == before
    assert world.state.village.stocks["forge"]["iron-ore"] == 2
    assert act(world, "village_smelt_iron")["code"] == "SMELTING_STARTED"
    assert world.state.village.stocks["forge"]["iron"] == 0
    assert world.state.village.stocks["forge"]["logs"] == 1
    village.advance(world, 30)
    assert world.state.village.stocks["forge"]["iron"] == 1
    perform(world, "village_smelt_iron", minutes=30)
    assert world.state.village.stocks["forge"]["iron"] == 2
    assert act(world, "village_smelt_iron")["code"] == "DEMAND_MET"
    assert world.state.village.time_use["torvin"].work >= 120
    assert world.state.village.coins == before


def test_missing_tools_fuel_and_incompatible_body_reject_without_free_output(tmp_path):
    world = make_world(tmp_path)
    world.actor.location_id = "iron-mine"
    world.state.entities["mining-pick"].location = "forge"
    assert act(world, "village_mine_ore")["code"] == "MINING_TOOL_REQUIRED"
    world.state.entities["mining-pick"].location = "iron-mine"
    world.actor.body.grips = ["paw"]
    assert act(world, "village_mine_ore")["code"] == "MINING_BODY_REQUIREMENT"
    world.actor.body.grips = ["hand"]
    perform(world, "village_mine_ore", minutes=30)
    perform(world, "village_load_ore", quantity=1)
    world.actor.location_id = "forge"
    perform(world, "village_unload_goods")
    village_goods.take(world, "forge", "logs", 2)
    assert act(world, "village_smelt_iron")["code"] == "SMELTING_SUPPLIES"
    assert world.state.village.stocks["forge"]["iron-ore"] == 1
    assert any(item["product"] == "logs" for item in village_economy.suggestions(world))


def test_interrupted_batches_do_not_invent_output_or_refund_fuel(tmp_path):
    world = make_world(tmp_path)
    world.actor.location_id = "iron-mine"
    act(world, "village_mine_ore")
    village.advance(world, 10)
    assert act(world, "village_mining_pause")["code"] == "MINING_PAUSED"
    assert world.state.village.stocks["iron-mine"]["iron-ore"] == 0
    village.advance(world, 5)
    perform(world, "village_mine_ore", minutes=30)
    perform(world, "village_load_ore", quantity=1)
    world.actor.location_id = "forge"
    perform(world, "village_unload_goods")
    act(world, "village_smelt_iron")
    village.advance(world, 5)
    world.state.entities["smelting-furnace"].health = 0
    village.advance(world, 5)
    assert world.state.village.mining.chunks[-1].status == "interrupted"
    assert world.state.village.stocks["forge"]["iron"] == 0
    assert world.state.village.stocks["forge"]["iron-ore"] == 0
    assert world.state.village.stocks["forge"]["logs"] == 1


def test_configuring_existing_town_only_adds_empty_mine_and_preserves_progress(tmp_path):
    world = make_world(tmp_path, mining=False)
    old_entities = copy.deepcopy(world.state.entities)
    old_stocks = copy.deepcopy(world.state.village.stocks)
    old_coins = copy.deepcopy(world.state.village.coins)
    request = {"mining": {"miner": "torvin"}}
    revision = world.revision()
    result = village_extensions.configure(world, "add-mine", revision, request)
    assert village_extensions.configure(world, "add-mine", revision, request) == result
    assert all(world.state.entities[key] == value for key, value in old_entities.items())
    assert all(world.state.village.stocks[key] == value for key, value in old_stocks.items())
    assert world.state.village.stocks["iron-mine"] == {"iron-ore": 0}
    assert world.state.village.coins == old_coins and world.state.village.minute == 600
    world.save()
    loaded = World(world.scenario, world.path, "mining", "torvin", "session-torvin")
    assert loaded.state.village.mining == world.state.village.mining
    assert loaded.state.entities["iron-mine"].kind == "location"
    before = loaded.state.model_dump(mode="json")
    with pytest.raises(ValueError):
        village_extensions.configure(loaded, "bad-miner", loaded.revision(), {"mining": {"miner": "jory"}})
    assert loaded.state.model_dump(mode="json") == before


def test_mining_reservations_survive_reload_and_meals_keep_priority(tmp_path):
    world = make_world(tmp_path)
    world.actor.location_id = "iron-mine"
    act(world, "village_mine_ore")
    world.save()
    loaded = World(world.scenario, world.path, "mining", "torvin", "session-torvin")
    village.advance(loaded, 30)
    assert loaded.state.village.stocks["iron-mine"]["iron-ore"] == 1
    loaded.state.village.minute = 1130
    assert act(loaded, "village_mine_ore")["code"] == "MEAL_CONFLICT"
    loaded.state.village.minute = 1140
    village.advance(loaded, 5)
    assert act(loaded, "village_mine_ore")["code"] == "MEAL_DUE"
