import copy

import pytest
from pydantic import ValidationError

from npc_sim import village, village_equipment
from npc_sim.living_village import build_scenario
from npc_sim.models import VillageEquipmentConfig
from npc_sim.storage import read_json
from npc_sim.world import World
from test_village import ROOT
from test_village_expanded import scenario
from test_world import act


def living_world(tmp_path):
    value = build_scenario(scenario())
    world = World(value, tmp_path / "equipment.json", "equipment", "torvin", "session-torvin")
    for agent in value.agents:
        world.bind(agent.id, f"session-{agent.id}")
    return world


def test_recommended_assets_cover_production_tools_transport_and_communal_water(tmp_path):
    world = living_world(tmp_path)
    assets = world.state.village.equipment.config.assets
    assert {"mill-drive", "bakery-oven", "village-wagon", "village-pump", "village-well"} <= set(assets)
    assert assets["village-wagon"].tiers[1].capacity == 8
    assert assets["village-well"].category == "water"


def test_portable_tool_and_non_portable_interactable_work_without_village(tmp_path):
    data = read_json(ROOT / "data" / "scenarios" / "forgotten-workshop.json")
    saw = "mallet"
    boulder = "cabinet"
    data["equipment"] = {"assets": {
        "sharp-saw": {"entity": saw, "location": data["entities"][saw]["location"],
                      "category": "interaction", "tiers": [
                          {"level": 0, "name": "Dull saw", "effects": {"cutting": 1}},
                          {"level": 1, "name": "Sharpened saw", "effects": {"cutting": 2}},
                      ]},
        "ancient-boulder": {"entity": boulder, "location": data["entities"][boulder]["location"],
                            "category": "environment", "communal": True,
                            "tiers": [{"level": 0, "name": "Uncleared boulder",
                                       "effects": {"path_open": False}}]},
    }}
    from npc_sim.models import Scenario
    value = Scenario.model_validate(data)
    world = World(value, tmp_path / "adventure-equipment.json", "adventure-equipment", "npc-001", "session")
    assert world.state.equipment is not None
    observed = world.observe()
    facts = {item["id"]: item for item in observed["visible_entities"]}
    if saw in facts:
        assert facts[saw]["equipment"]["effects"] == {"cutting": 1}
    assert "Dull saw" in world.description(world.state.entities[saw])
    assert world.state.equipment.assets["ancient-boulder"].tier == 0


def test_invalid_or_duplicate_asset_references_are_rejected(tmp_path):
    value = scenario()
    from npc_sim.models import Scenario
    invalid = value.model_copy(deep=True)
    invalid.equipment = VillageEquipmentConfig.model_validate({"assets": {"bad": {
            "entity": "missing", "location": "forge", "tiers": [{"level": 0, "name": "Bad"}]}}})
    with pytest.raises(ValidationError):
        Scenario.model_validate(invalid.model_dump(mode="json"))
    config = VillageEquipmentConfig.model_validate({"assets": {"saw": {
        "entity": "saw", "location": "workshop", "owner": "hobb", "tiers": [
            {"level": 0, "name": "Saw"}, {"level": 1, "name": "Fine saw", "duration": 30,
             "cost": 1, "supplies": {"not-real": 1}}]}}})
    value.village.equipment = config
    with pytest.raises(ValidationError):
        Scenario.model_validate(value.model_dump(mode="json"))


def test_funded_upgrade_consumes_real_money_and_supplies_then_changes_tier(tmp_path):
    world = living_world(tmp_path)
    world.state.village.minute = 720
    torvin = world.state.village.residents["torvin"]
    if torvin.life:
        torvin.life.sleeping = False
        torvin.life.sleep_until = None
        torvin.life.last_woke = 720
    torvin.available_at = 720
    definition = world.state.village.equipment.config.assets["village-well"]
    world.state.village.well_dug = True
    world.state.village.stocks.setdefault("tavern-yard", {}).update({"planks": 2, "iron": 2})
    from npc_sim.models import VillageStockLot
    world.state.village.stock_lots.setdefault("tavern-yard", {})
    for resource, quantity in {"planks": 2, "iron": 2}.items():
        world.state.village.stock_lots["tavern-yard"][resource] = [
            VillageStockLot(quantity=quantity, produced_at=720)]
    world.state.village.coins["torvin"] = 50
    world.actor.location_id = "tavern-yard"
    definition.users.append("torvin")
    world.state.requests.clear()
    before_coins = world.state.village.coins["torvin"]
    before_planks = world.state.village.stocks["tavern-yard"]["planks"]
    result = world.act("upgrade-well", world.revision(), "village_upgrade_equipment",
                       {"asset": "village-well", "payer": "torvin"})
    assert result["code"] == "EQUIPMENT_UPGRADE_STARTED"
    assert world.state.village.coins["torvin"] == before_coins - 30
    assert world.state.village.stocks["tavern-yard"]["planks"] == before_planks - 1
    village.advance(world, 60)
    assert world.state.village.equipment.assets["village-well"].tier == 1
    assert village_equipment.water_capacity(world) == 4


def test_upgrade_state_survives_reload_and_wagon_capacity_is_real(tmp_path):
    world = living_world(tmp_path)
    world.state.village.equipment.assets["village-wagon"].tier = 1
    world.save()
    loaded = World(world.scenario, world.path, "equipment", "torvin", "session-torvin")
    assert village_equipment.transport_capacity(loaded, 6) == 8


def test_legacy_scenarios_keep_equipment_opt_in(tmp_path):
    value = scenario()
    assert value.equipment is None and value.village.equipment is None
    world = World(value, tmp_path / "legacy.json", "legacy", "torvin", "session-torvin")
    assert world.state.equipment is None
    assert world.state.village.equipment is None
