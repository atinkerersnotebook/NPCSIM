import json

import pytest
from pydantic import ValidationError

from npc_sim.controller import Controller
from npc_sim.models import Entity, Nutrition, Scenario
from npc_sim.storage import read_json
from npc_sim.world import World
from test_controller import ROOT, TestProxy, configuration
from test_world import act


ROUTE = [
    ("read", {"target": "postit-note"}),
    ("open", {"target": "cupboard"}),
    ("take", {"target": "knife"}),
    ("open", {"target": "bread-bin"}),
    ("slice", {"target": "bread-loaf", "item": "knife"}),
    ("open", {"target": "peanut-butter-jar"}),
    ("open", {"target": "jelly-jar"}),
    ("spread", {"target": "bread-a", "ingredient": "peanut-butter", "item": "knife"}),
    ("spread", {"target": "bread-b", "ingredient": "jelly", "item": "knife"}),
    ("assemble", {"first": "bread-a", "second": "bread-b"}),
    ("serve", {"target": "pbj-sandwich", "surface": "plate"}),
]


@pytest.fixture
def world(tmp_path):
    scenario = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "sandwich-kitchen.json"))
    return World(scenario, tmp_path / "world-state.json", "world", "npc", "session")


def prepare(world, count=len(ROUTE)):
    for action, arguments in ROUTE[:count]:
        assert act(world, action, **arguments)["outcome"] == "APPLIED"


def nutrients(world):
    result = {}
    for entity in world.state.entities.values():
        if entity.food and entity.location is not None and entity.nutrition is not None:
            for key, value in entity.nutrition.model_dump().items():
                result[key] = result.get(key, 0) + value
    return result


def test_initial_kitchen_hides_contents_recipes_text_and_nutrition(world):
    initial = world.observe()
    assert initial["opening"]
    assert world.actor.body.capacity == 1
    assert world.evaluate_goal("entity_prepared")["status"] == "active"
    assert {entity["id"] for entity in initial["visible_entities"]} == {
        "counter", "cupboard", "bread-bin", "fruit-bin", "refrigerator", "postit-note",
        "stove", "power-outlet", "peanut-butter-maker"
    }
    for hidden in ("bread-loaf", "knife", "plate", "peanut-butter-jar", "jelly-jar", "tomato", "cheese", "ham"):
        assert f'"{hidden}"' not in json.dumps(initial)
    assert "readable_text" not in json.dumps(initial)
    note = next(entity for entity in initial["visible_entities"] if entity["id"] == "postit-note")
    assert note["readable"] and "text" not in note
    world.toggle_need("npc", "food", True)
    world.actor.needs["food"].rate = 4
    result = act(world, "read", target="postit-note")
    assert result["code"] == "READ"
    assert "Make a peanut butter and jam sandwich" in result["explanation"]
    assert not result["observation"]["opening"]
    assert world.actor.action_clock == 0
    assert world.actor.needs["food"].level == 0
    assert act(world, "read", target="postit-note")["outcome"] == "NO_CHANGE"
    act(world, "open", target="cupboard")
    assert "peanut-butter" not in world.visible_ids()
    assert "jelly" not in world.visible_ids()
    assert "peanuts" in world.visible_ids()
    assert not any(entity["id"] in {"peanut-butter", "jelly"} for entity in world.observe()["visible_entities"])
    act(world, "open", target="peanut-butter-jar")
    peanut = next(entity for entity in world.observe()["visible_entities"] if entity["id"] == "peanut-butter")
    assert peanut["nutrition"]["calories_kcal"] == 118
    assert "jelly" not in world.visible_ids()


def test_slicing_requires_a_local_intact_carried_tool_and_preserves_nutrition(world):
    act(world, "open", target="cupboard")
    act(world, "open", target="bread-bin")
    before = nutrients(world)
    assert act(world, "slice", target="bread-loaf", item="knife")["code"] == "NOT_CARRIED"
    act(world, "take", target="knife")
    assert act(world, "take", target="bread-loaf")["code"] == "CAPACITY_EXCEEDED"
    request = ("cut-once", world.revision(), "slice", {"target": "bread-loaf", "item": "knife"})
    result = world.act(*request)
    assert result["outcome"] == "APPLIED"
    assert world.state.entities["bread-loaf"].location is None
    for eid in ("bread-a", "bread-b"):
        assert world.state.entities[eid].nutrition.model_dump() == pytest.approx({
            key: value / 2 for key, value in world.scenario.entities["bread-loaf"].nutrition.model_dump().items()
        })
    assert nutrients(world) == pytest.approx(before)
    revision, clock = world.revision(), world.actor.action_clock
    assert world.act(*request) == result
    assert (world.revision(), world.actor.action_clock) == (revision, clock)
    assert act(world, "slice", target="bread-loaf", item="knife")["outcome"] == "REJECTED"
    assert world.actor.inventory == ["knife"]
    assert world.inventory_units_used() == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_correct_sandwich_requires_real_ingredients_and_separate_plating(world, reverse):
    before = nutrients(world)
    prepare(world, 7)
    assert act(world, "assemble", first="bread-a", second="bread-b")["outcome"] == "REJECTED"
    for ingredient, target in (
        ("peanut-butter", "bread-b" if reverse else "bread-a"),
        ("jelly", "bread-a" if reverse else "bread-b"),
    ):
        assert act(world, "spread", target=target, ingredient=ingredient, item="knife")["outcome"] == "APPLIED"
        assert world.state.entities[ingredient].location is None
    assert act(world, "spread", target="bread-a", ingredient="peanut-butter", item="knife")["outcome"] == "REJECTED"
    assert act(world, "assemble", first="bread-a", second="bread-b")["outcome"] == "APPLIED"
    sandwich = world.state.entities["pbj-sandwich"]
    assert sandwich.location == "counter"
    assert world.evaluate_goal("entity_prepared")["status"] == "active"
    assert sandwich.nutrition.model_dump() == pytest.approx({
        "serving_grams": 120, "calories_kcal": 378, "protein_g": 12,
        "carbohydrates_g": 57, "fat_g": 12, "fiber_g": 3.8, "sugar_g": 16,
    })
    assert act(world, "serve", target="pbj-sandwich", surface="bread-bin")["outcome"] == "APPLIED"
    assert world.evaluate_goal("entity_prepared")["status"] == "active"
    assert act(world, "serve", target="pbj-sandwich", surface="plate")["outcome"] == "APPLIED"
    verification = world.evaluate_goal("entity_prepared")
    assert verification["status"] == "achieved"
    assert verification["evidence"]["nutrition"]["calories_kcal"] == 378
    assert nutrients(world) == pytest.approx(before)
    assert world.actor.inventory == ["knife"]
    reloaded = World(world.scenario, world.path, "world", "npc", "session")
    assert reloaded.evaluate_goal("entity_prepared")["status"] == "achieved"
    assert reloaded.state.entities["pbj-sandwich"].nutrition == sandwich.nutrition
    assert reloaded.actor.read_entities == ["postit-note"]
    reloaded.state.entities["plate"].health = 0
    assert reloaded.evaluate_goal("entity_prepared")["status"] == "active"


def test_distractors_and_wrong_spreads_are_not_the_goal(world):
    prepare(world, 7)
    act(world, "open", target="refrigerator")
    act(world, "serve", target="ham", surface="plate")
    assert world.evaluate_goal("entity_prepared")["status"] == "active"
    assert act(world, "spread", target="bread-a", ingredient="ham", item="knife")["outcome"] == "REJECTED"
    assert act(world, "spread", target="bread-a", ingredient="butter", item="knife")["outcome"] == "APPLIED"
    assert world.state.entities["bread-a"].nutrition.calories_kcal == 177
    act(world, "spread", target="bread-b", ingredient="jelly", item="knife")
    assert act(world, "assemble", first="bread-a", second="bread-b")["outcome"] == "REJECTED"
    assert "pbj-sandwich" not in world.state.entities
    assert world.evaluate_goal("entity_prepared")["status"] == "active"


@pytest.mark.parametrize("food,action,outputs", [
    ("tomato", "slice", ["tomato-a", "tomato-b"]),
    ("tomato", "dice", ["diced-tomato"]),
    ("cheese", "slice", ["cheese-a", "cheese-b"]),
])
def test_optional_food_processing_conserves_every_nutrient(world, food, action, outputs):
    act(world, "open", target="cupboard")
    act(world, "take", target="knife")
    act(world, "open", target="fruit-bin" if food == "tomato" else "refrigerator")
    before = nutrients(world)
    assert act(world, action, target=food, item="knife")["outcome"] == "APPLIED"
    assert world.state.entities[food].location is None
    assert set(outputs) <= set(world.visible_ids())
    assert nutrients(world) == pytest.approx(before)
    if action == "slice" and food == "tomato":
        for eid in outputs:
            assert act(world, "dice", target=eid, item="knife")["outcome"] == "APPLIED"
        assert nutrients(world) == pytest.approx(before)
    assert world.evaluate_goal("entity_prepared")["status"] == "active"


def test_remote_food_and_broken_tools_cannot_be_used(world):
    prepare(world, 4)
    world.state.entities["bread-loaf"].location = "remote-room"
    assert act(world, "slice", target="bread-loaf", item="knife")["outcome"] == "REJECTED"
    world.state.entities["bread-loaf"].location = "bread-bin"
    world.state.entities["knife"].health = 0
    assert act(world, "slice", target="bread-loaf", item="knife")["outcome"] == "REJECTED"
    assert "bread-a" not in world.state.entities


def test_unknown_nutrition_remains_unknown_after_processing(world):
    world.state.entities["bread-loaf"].nutrition = None
    prepare(world, 10)
    assert world.state.entities["bread-a"].nutrition is None
    assert world.state.entities["pbj-sandwich"].nutrition is None
    assert next(entity for entity in world.observe()["visible_entities"] if entity["id"] == "pbj-sandwich")["nutrition"] is None


@pytest.mark.parametrize("bad", [-1, float("inf"), float("nan")])
def test_nutrition_rejects_invalid_values(world, bad):
    for key in Nutrition.model_fields:
        values = world.scenario.entities["bread-loaf"].nutrition.model_dump()
        values[key] = bad
        with pytest.raises(ValidationError):
            Nutrition.model_validate(values)
    with pytest.raises(ValidationError, match="Only food"):
        Entity(name="Rock", kind="item", description="A rock.", nutrition=world.scenario.entities["bread-loaf"].nutrition)
    assert Entity(name="Old bread", kind="item", description="Bread.", food=True).nutrition is None


@pytest.mark.asyncio
async def test_kitchen_solution_uses_unchanged_controller_and_real_mcp(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    fake = TestProxy(controller.proxy, ROUTE)
    controller.proxy = fake
    await controller.start_worker()
    try:
        await controller.command("start", {"scenario_id": "sandwich-kitchen"})
        for _ in ROUTE:
            await controller.command("step")
        assert controller.run["status"] == "succeeded"
        assert controller.run["reason"] == "The world verified that this NPC achieved its goal."
        assert controller.run["actions"] == 11
        assert controller.run["verification"]["status"] == "achieved"
        assert '"bread-loaf"' not in json.dumps(fake.contexts[0])
        assert '"peanut-butter"' not in json.dumps(fake.contexts[0])
        assert "text" not in fake.contexts[0]["memory"]["known_entities"]["postit-note"]
        assert "Make a peanut butter and jam sandwich" in fake.contexts[1]["memory"]["known_entities"]["postit-note"]["text"]
        saved = read_json(controller.world.state_path)
        assert saved["entities"]["pbj-sandwich"]["location"] == "plate"
        assert saved["entities"]["pbj-sandwich"]["nutrition"]["calories_kcal"] == 378
        assert saved["actors"][controller.run["actor_id"]]["inventory"] == ["knife"]
        assert config.world.scenario == "escape-room"
    finally:
        await controller.shutdown()
