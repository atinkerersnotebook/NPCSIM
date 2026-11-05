import pytest
from pydantic import ValidationError

from npc_sim.models import AgentProfile, Body, BreadBatch, Entity, Recipe, Scenario
from npc_sim.storage import read_json
from npc_sim.world import World
from test_world import ROOT, act


def badger_body():
    return Body(description="A badger without hands.",
                capabilities=["move", "manipulate", "strength", "rigging", "digging"],
                capacity=10, grips=["paw", "mouth"], max_held_items=2)


def make_world(tmp_path, scenario_id="escape-room", body=None):
    scenario = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / f"{scenario_id}.json"))
    if body is not None:
        scenario.body = body
    return World(scenario, tmp_path / "world.json", "world", "npc", "session")


def prop(world, eid, *, adapted=False):
    entity = Entity(name=eid, kind="item", description="A test prop.",
                    location=world.actor.location_id, takable=True,
                    carry_grips=["paw", "mouth", "hand"] if adapted else ["hand"],
                    use_grips=["paw", "mouth", "hand"] if adapted else ["hand"])
    world.state.entities[eid] = entity
    return entity


def descriptor(world, action):
    return next(value for value in world.actions() if value["id"] == action)


def test_badger_cannot_pick_up_an_ordinary_bowl_despite_strength_and_capacity(tmp_path):
    world = make_world(tmp_path, body=badger_body())
    bowl = prop(world, "bowl")
    result = act(world, "take", target="bowl")
    assert result["code"] == "BODY_HANDLING_REQUIRED"
    assert "hand grip" in result["explanation"]
    assert "paw, mouth" in result["explanation"]
    assert "Strength" in result["explanation"]
    assert world.actor.inventory == []
    assert bowl.location == world.actor.location_id
    assert "hand grip" in descriptor(world, "take")["target_requirements"]["bowl"]


def test_legacy_human_body_can_pick_up_the_same_ordinary_bowl(tmp_path):
    world = make_world(tmp_path)
    prop(world, "bowl")
    assert world.actor.body.grips == ["hand"]
    assert world.actor.body.max_held_items is None
    assert act(world, "take", target="bowl")["outcome"] == "APPLIED"


def test_actual_badger_profile_cannot_take_an_actual_bakery_bowl(tmp_path):
    profile = AgentProfile.model_validate(read_json(ROOT / "data" / "agents" / "badger.json"))
    world = make_world(tmp_path, "bread-bakery", profile.body_for("bread-bakery", "npc_only"))
    world.state.entities["bowl-cupboard"].open = True
    result = act(world, "take", target="batch-a")
    assert result["code"] == "BODY_HANDLING_REQUIRED"
    assert world.actor.body.capacity >= world.state.entities["batch-a"].inventory_units
    assert world.actor.inventory == []
    assert world.state.entities["batch-a"].location == "bowl-cupboard"


def test_adapted_props_and_mixed_target_descriptors(tmp_path):
    world = make_world(tmp_path, body=badger_body())
    prop(world, "bowl")
    prop(world, "looped-pin", adapted=True)
    action = descriptor(world, "take")
    assert action["availability"] == "available"
    assert "bowl" in action["target_requirements"]
    assert not action["target_requirements"].get("looped-pin")
    assert act(world, "take", target="looped-pin")["outcome"] == "APPLIED"


def test_held_object_limit_is_independent_of_weight_and_drop_releases_it(tmp_path):
    body = Body(description="One mouth.", capabilities=["move", "manipulate"],
                capacity=10, grips=["mouth"], max_held_items=1)
    world = make_world(tmp_path, body=body)
    for eid in ["a", "b"]:
        prop(world, eid, adapted=True)
    assert act(world, "take", target="a")["outcome"] == "APPLIED"
    result = act(world, "take", target="b")
    assert result["code"] == "BODY_HANDLING_REQUIRED"
    assert "at most 1" in result["explanation"]
    assert world.actor.inventory == ["a"]
    assert act(world, "drop", target="a")["outcome"] == "APPLIED"
    assert act(world, "take", target="b")["outcome"] == "APPLIED"


def test_handoffs_validate_recipient_anatomy_and_leave_both_inventories_unchanged(tmp_path):
    world = make_world(tmp_path)
    recipient = world.bind("badger", "badger-session")
    recipient.actor.body = badger_body()
    bowl = prop(world, "bowl")
    assert act(world, "take", target="bowl")["outcome"] == "APPLIED"
    result = act(world, "give", target="bowl", recipient="badger")
    assert result["code"] == "BODY_HANDLING_REQUIRED"
    assert "cannot receive" in result["explanation"]
    assert world.actor.inventory == ["bowl"]
    assert recipient.actor.inventory == []
    assert bowl.location == world.actor_id
    bowl.carry_grips = ["mouth"]
    assert act(world, "give", target="bowl", recipient="badger")["outcome"] == "APPLIED"


def test_handoff_preserves_a_valid_recipient_and_enforces_occupied_grips(tmp_path):
    world = make_world(tmp_path)
    badger = world.bind("badger", "badger-session")
    badger.actor.body = Body(description="One grip.", capabilities=["manipulate"],
                             capacity=10, grips=["mouth"], max_held_items=0)
    human = world.bind("human", "human-session")
    prop(world, "bowl", adapted=True)
    assert act(world, "take", target="bowl")["outcome"] == "APPLIED"
    assert descriptor(world, "give")["availability"] == "available"
    assert act(world, "give", target="bowl", recipient="badger")["code"] == "BODY_HANDLING_REQUIRED"
    assert act(world, "give", target="bowl", recipient=human.actor_id)["outcome"] == "APPLIED"


@pytest.mark.parametrize(("action", "stage", "extra"), [
    ("measure", "measuring", {"ingredient": "flour"}),
    ("mix_dough", "measuring", {"equipment": "mixer"}),
    ("serve_bread", "cooled", {}),
])
def test_stationary_bakery_bowls_cannot_bypass_anatomy(tmp_path, action, stage, extra):
    world = make_world(tmp_path, "bread-bakery", badger_body())
    world.state.entities["bowl-cupboard"].open = True
    world.state.entities["batch-a"].location = world.actor.location_id
    world.state.entities["flour"].location = world.actor.location_id
    world.state.batches["batch-a"] = BreadBatch(stage=stage)
    world.tick()
    before_entities = {eid: value.model_dump() for eid, value in world.state.entities.items()}
    before_stock = world.state.ingredient_stock.copy()
    before_batch = world.state.batches["batch-a"].model_dump()
    assert descriptor(world, action)["availability"] == "blocked"
    result = act(world, action, target="batch-a", **extra)
    assert result["code"] == "BODY_HANDLING_REQUIRED"
    assert world.state.ingredient_stock == before_stock
    assert world.state.batches["batch-a"].model_dump() == before_batch
    assert {eid: value.model_dump() for eid, value in world.state.entities.items()} == before_entities


def test_stationary_recipe_cannot_bypass_anatomy_or_consume_ingredients(tmp_path):
    world = make_world(tmp_path, body=badger_body())
    bowl = prop(world, "bowl")
    prop(world, "spoon", adapted=True)
    world.scenario.recipe_actions["stir"] = "Stir the bowl with a spoon."
    world.scenario.recipes.append(Recipe(action_id="stir", arguments={"target": "bowl"},
                                         carried=["spoon"], consumes=[], updates={"bowl": {"stirred": "yes"}},
                                         explanation="Stirred."))
    assert act(world, "take", target="spoon")["outcome"] == "APPLIED"
    assert descriptor(world, "stir")["availability"] == "blocked"
    assert act(world, "stir", target="bowl")["code"] == "BODY_HANDLING_REQUIRED"
    assert bowl.preparation == {}


def test_tool_operation_and_drop_even_if_an_incompatible_item_is_already_carried(tmp_path):
    world = make_world(tmp_path, body=badger_body())
    prop(world, "bowl")
    world.state.entities["appliance"] = Entity(
        name="Appliance", kind="mixer", description="Hand-operated switch.",
        location=world.actor.location_id, power="off")
    assert act(world, "turn_on", target="appliance")["code"] == "BODY_HANDLING_REQUIRED"
    assert world.state.entities["appliance"].power == "off"
    world.actor.inventory.append("bowl")
    world.state.entities["bowl"].location = world.actor_id
    assert act(world, "drop", target="bowl")["outcome"] == "APPLIED"


def test_hitting_requires_control_of_the_tool_not_gripping_the_target(tmp_path):
    world = make_world(tmp_path, body=badger_body())
    target = prop(world, "bowl")
    prop(world, "adapted-tool", adapted=True).damage = 5
    assert act(world, "take", target="adapted-tool")["outcome"] == "APPLIED"
    assert act(world, "hit", target="bowl", item="adapted-tool")["outcome"] == "APPLIED"
    assert target.health == 95


def test_skill_checks_require_compatible_control_and_tool_grips(tmp_path):
    scenario = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "sealed-vault.json"))
    thief = next(agent for agent in scenario.agents if agent.id == "thief")
    assert thief.body is not None
    thief.body.grips = ["paw", "mouth"]
    world = World(scenario, tmp_path / "world.json", "world", "thief", "session")
    result = act(world, "pick_gallery_lock", target="gallery-lock")
    assert result["code"] == "BODY_HANDLING_REQUIRED"
    assert not world.state.entities["gallery-door"].open
    assert "pick_gallery_lock" not in world.state.skill_uses
    world.state.entities["gallery-lock"].use_grips = ["paw"]
    result = act(world, "pick_gallery_lock", target="gallery-lock")
    assert result["code"] == "BODY_HANDLING_REQUIRED"
    assert "lockpicks" in result["explanation"].lower()
    world.actor.body.capabilities.remove("lockpicking")
    assert act(world, "pick_gallery_lock", target="gallery-lock")["code"] == "BODY_CAPABILITY_REQUIRED"


def test_observation_and_persistence_preserve_explicit_grips(tmp_path):
    world = make_world(tmp_path, body=badger_body())
    prop(world, "looped-pin", adapted=True)
    assert act(world, "take", target="looped-pin")["outcome"] == "APPLIED"
    reloaded = World(world.scenario, world.path, "world", "npc", "session")
    assert reloaded.actor.body.grips == ["paw", "mouth"]
    assert reloaded.actor.body.max_held_items == 2
    assert reloaded.state.entities["looped-pin"].carry_grips == ["paw", "mouth", "hand"]
    observation = reloaded.observe()
    assert observation["self"]["body"]["grips"] == ["paw", "mouth"]
    assert observation["self"]["body"]["max_held_items"] == 2
    assert next(entity for entity in observation["visible_entities"] if entity["id"] == "looped-pin")[
        "use_grips"] == ["paw", "mouth", "hand"]


@pytest.mark.parametrize("changes", [
    {"grips": ["tentacle"]}, {"max_held_items": -1},
    {"max_held_items": True}, {"max_held_items": 1.5},
])
def test_invalid_anatomy_configuration_is_rejected(changes):
    with pytest.raises(ValidationError):
        Body(description="Body", capabilities=["manipulate"], capacity=10, **changes)


def test_reading_and_observation_do_not_require_hands(tmp_path):
    world = make_world(tmp_path, body=badger_body())
    prop(world, "notice").readable_text = "A readable notice."
    assert act(world, "look", target="notice")["outcome"] == "APPLIED"
    assert act(world, "read", target="notice")["outcome"] == "APPLIED"
