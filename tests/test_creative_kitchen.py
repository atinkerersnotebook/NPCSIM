import json

import pytest

from npc_sim.controller import Controller
from npc_sim.models import Entity, Scenario
from pydantic import ValidationError
from test_controller import ROOT, TestProxy, configuration
from test_kitchen import ROUTE, nutrients, prepare, world
from test_world import act


CREATIVE_ROUTE = [
    ("read", {"target": "postit-note"}),
    ("open", {"target": "cupboard"}),
    ("open", {"target": "bread-bin"}),
    ("take", {"target": "knife"}),
    ("slice", {"target": "bread-loaf", "item": "knife"}),
    ("open", {"target": "fruit-bin"}),
    ("slice", {"target": "banana", "item": "knife"}),
    ("drop", {"target": "knife"}),
    ("take", {"target": "frying-pan"}),
    ("place", {"target": "frying-pan", "surface": "stove"}),
    ("open", {"target": "refrigerator"}),
    ("plug", {"target": "peanut-butter-maker", "outlet": "power-outlet"}),
    ("turn_on", {"target": "peanut-butter-maker"}),
    ("grind", {"target": "peanuts", "machine": "peanut-butter-maker"}),
    ("turn_off", {"target": "peanut-butter-maker"}),
    ("unplug", {"target": "peanut-butter-maker"}),
    ("plug", {"target": "stove", "outlet": "power-outlet"}),
    ("turn_on", {"target": "stove"}),
    ("cook", {"target": "bacon", "pan": "frying-pan", "stove": "stove"}),
    ("turn_off", {"target": "stove"}),
    ("unplug", {"target": "stove"}),
    ("take", {"target": "knife"}),
    ("open", {"target": "blackberry-jam-jar"}),
    ("spread", {"target": "bread-a", "ingredient": "fresh-peanut-butter", "item": "knife"}),
    ("spread", {"target": "bread-b", "ingredient": "blackberry-jam", "item": "knife"}),
    ("add", {"target": "bread-a", "ingredient": "cooked-bacon", "item": "knife"}),
    ("add", {"target": "bread-a", "ingredient": "banana-a", "item": "knife"}),
    ("spread", {"target": "bread-a", "ingredient": "honey", "item": "knife"}),
    ("assemble", {"first": "bread-a", "second": "bread-b"}),
    ("serve", {"target": "pbj-sandwich", "surface": "plate"}),
]


@pytest.mark.parametrize("jam", ["jelly", "blackberry-jam", "strawberry-jam"])
@pytest.mark.parametrize("same_slice", [True, False])
def test_any_jam_and_both_spreads_on_either_slice_can_pass(world, jam, same_slice):
    before = nutrients(world)
    prepare(world, 7)
    jar = "jelly-jar" if jam == "jelly" else f"{jam}-jar"
    act(world, "open", target=jar)
    assert act(world, "spread", target="bread-a", ingredient="peanut-butter", item="knife")["outcome"] == "APPLIED"
    assert act(world, "spread", target="bread-a" if same_slice else "bread-b", ingredient=jam, item="knife")["outcome"] == "APPLIED"
    assert act(world, "assemble", first="bread-a", second="bread-b")["outcome"] == "APPLIED"
    assert world.evaluate_goal("entity_prepared")["status"] == "active"
    act(world, "serve", target="pbj-sandwich", surface="plate")
    result = world.evaluate_goal("entity_prepared")
    assert result["status"] == "achieved"
    assert "peanut butter" in result["evidence"]["ingredients"]
    assert nutrients(world) == pytest.approx(before)


def test_almond_butter_is_an_extra_not_a_peanut_butter_substitute(world):
    prepare(world, 7)
    act(world, "open", target="almond-butter-jar")
    act(world, "spread", target="bread-a", ingredient="almond-butter", item="knife")
    act(world, "spread", target="bread-b", ingredient="jelly", item="knife")
    assert act(world, "assemble", first="bread-a", second="bread-b")["outcome"] == "REJECTED"
    act(world, "spread", target="bread-a", ingredient="peanut-butter", item="knife")
    assert act(world, "assemble", first="bread-a", second="bread-b")["outcome"] == "APPLIED"
    assert "almond butter" in world.state.entities["pbj-sandwich"].ingredients


def test_creative_meal_uses_real_power_pan_and_ingredients_and_preserves_nutrition(world):
    before = nutrients(world)
    for action, args in CREATIVE_ROUTE:
        result = act(world, action, **args)
        assert result["outcome"] == "APPLIED", (action, args, result)
        assert world.inventory_units_used() <= 1
    sandwich = world.state.entities["pbj-sandwich"]
    assert sandwich.location == "plate"
    assert set(sandwich.ingredients) == {"bread", "peanut butter", "blackberry jam", "cooked bacon", "banana", "honey"}
    assert sandwich.nutrition.serving_grams == 200
    assert sandwich.nutrition.calories_kcal == 576.5
    assert nutrients(world) == pytest.approx(before)
    for eid in ("peanuts", "bacon", "fresh-peanut-butter", "cooked-bacon", "banana-a", "honey", "blackberry-jam"):
        assert world.state.entities[eid].location is None
    assert world.state.entities["banana-b"].location == "counter"
    reloaded = type(world)(world.scenario, world.path, "world", "npc", "session")
    assert reloaded.state.entities["pbj-sandwich"].ingredients == sandwich.ingredients
    assert reloaded.evaluate_goal("entity_prepared")["status"] == "achieved"


def test_power_outlet_is_exclusive_and_switching_off_is_required(world):
    act(world, "open", target="cupboard")
    assert act(world, "turn_on", target="peanut-butter-maker")["code"] == "NO_POWER"
    assert "grind" not in {action["id"] for action in world.actions()}
    request = ("plug-once", world.revision(), "plug", {"target": "peanut-butter-maker", "outlet": "power-outlet"})
    first = world.act(*request)
    assert first["outcome"] == "APPLIED"
    assert world.act(*request) == first
    assert act(world, "plug", target="stove", outlet="power-outlet")["code"] == "OUTLET_OCCUPIED"
    act(world, "turn_on", target="peanut-butter-maker")
    assert act(world, "unplug", target="peanut-butter-maker")["code"] == "POWER_ON"
    assert "grind" in {action["id"] for action in world.actions()}
    act(world, "turn_off", target="peanut-butter-maker")
    assert "grind" not in {action["id"] for action in world.actions()}
    act(world, "unplug", target="peanut-butter-maker")
    assert act(world, "plug", target="stove", outlet="power-outlet")["outcome"] == "APPLIED"
    world.state.entities["power-outlet"].health = 0
    assert act(world, "turn_on", target="stove")["code"] == "NO_POWER"


def test_raw_bacon_and_unpositioned_or_unpowered_pan_cannot_be_used(world):
    prepare(world, 7)
    act(world, "open", target="refrigerator")
    assert act(world, "add", target="bread-a", ingredient="bacon", item="knife")["outcome"] == "REJECTED"
    assert act(world, "cook", target="bacon", pan="frying-pan", stove="stove")["outcome"] == "REJECTED"
    act(world, "plug", target="stove", outlet="power-outlet")
    act(world, "turn_on", target="stove")
    assert act(world, "cook", target="bacon", pan="frying-pan", stove="stove")["outcome"] == "REJECTED"
    assert act(world, "take", target="frying-pan")["code"] == "CAPACITY_EXCEEDED"
    act(world, "drop", target="knife")
    act(world, "take", target="frying-pan")
    act(world, "place", target="frying-pan", surface="stove")
    assert act(world, "cook", target="bacon", pan="frying-pan", stove="stove")["outcome"] == "APPLIED"


def test_recipe_is_neutral_and_unknown_nutrition_is_not_invented(world):
    text = world.scenario.entities["recipe-note"].readable_text.lower()
    assert all(word not in text for word in ("best", "elvis", "optimal", "highest", "passing"))
    prepare(world, 9)
    act(world, "open", target="fruit-bin")
    world.state.entities["avocado"].nutrition = None
    act(world, "slice", target="avocado", item="knife")
    act(world, "add", target="bread-a", ingredient="avocado-a", item="knife")
    act(world, "assemble", first="bread-a", second="bread-b")
    assert world.state.entities["pbj-sandwich"].nutrition is None
    assert "avocado" in world.state.entities["pbj-sandwich"].ingredients


@pytest.mark.asyncio
async def test_creative_route_works_through_real_mcp_and_stays_in_discovery_memory(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    controller.proxy = TestProxy(controller.proxy, CREATIVE_ROUTE)
    await controller.start_worker()
    try:
        await controller.command("start", {"scenario_id": "sandwich-kitchen"})
        assert "raw bacon" not in json.dumps(controller.memory)
        for _ in CREATIVE_ROUTE:
            await controller.command("step")
        assert controller.run["status"] == "succeeded"
        assert controller.run["successful"] is True
        assert controller.run["actions"] == 30
        assert controller.run["completion_analysis"]["final_verification"]["evidence"]["nutrition"]["calories_kcal"] == 576.5
        assert "cooked bacon" in json.dumps(controller.memory)
    finally:
        await controller.shutdown()


def test_invalid_power_and_recipe_references_are_rejected(world):
    with pytest.raises(ValidationError):
        Entity(name="Thing", kind="item", description="Thing", ingredients=["bread"])
    data = world.scenario.model_dump()
    data["entities"]["stove"]["plugged_into"] = "cupboard"
    with pytest.raises(ValidationError, match="outlet"):
        Scenario.model_validate(data)
    data["entities"]["stove"]["plugged_into"] = "power-outlet"
    data["entities"]["peanut-butter-maker"]["plugged_into"] = "power-outlet"
    with pytest.raises(ValidationError, match="only one"):
        Scenario.model_validate(data)


def test_a_broken_appliance_can_be_disconnected_to_free_the_socket(world):
    act(world, "plug", target="peanut-butter-maker", outlet="power-outlet")
    act(world, "turn_on", target="peanut-butter-maker")
    world.state.entities["peanut-butter-maker"].health = 0
    assert act(world, "turn_off", target="peanut-butter-maker")["outcome"] == "APPLIED"
    assert act(world, "unplug", target="peanut-butter-maker")["outcome"] == "APPLIED"
    assert act(world, "plug", target="stove", outlet="power-outlet")["outcome"] == "APPLIED"
