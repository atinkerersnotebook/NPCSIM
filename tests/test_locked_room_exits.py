import pytest
from pydantic import ValidationError

from npc_sim.models import AgentProfile, Body, Entity
from npc_sim.world import World
from test_world import ROOT, act, world
from npc_sim.storage import read_json


@pytest.mark.parametrize(("name", "exit_id"), [
    ("raven", "window"), ("sprite", "window"), ("otter", "drain"),
])
@pytest.mark.parametrize("skill_mode", ["scenario", "npc_only"])
def test_small_npcs_can_escape_by_their_appropriate_route(world, name, exit_id, skill_mode):
    profile = AgentProfile.model_validate(read_json(ROOT / "data" / "agents" / f"{name}.json"))
    world.actor.body = profile.body_for("escape-room", skill_mode)
    assert world.actor.body.size == "small"
    assert act(world, "go", target=exit_id)["outcome"] == "APPLIED"
    assert world.actor.location_id == "outside"
    assert world.evaluate_goal("actor_outside_start_location")["status"] == "achieved"
    assert not world.state.entities["door"].open
    assert world.state.entities["key"].location == "box"
    assert act(world, "go", target=exit_id)["outcome"] == "APPLIED"
    assert world.actor.location_id == "room"


@pytest.mark.parametrize(("name", "exit_id"), [("raven", "drain"), ("otter", "window")])
def test_small_size_does_not_grant_missing_route_abilities(world, name, exit_id):
    profile = AgentProfile.model_validate(read_json(ROOT / "data" / "agents" / f"{name}.json"))
    world.actor.body = profile.body
    result = act(world, "go", target=exit_id)
    assert result["code"] == "BODY_CAPABILITY_REQUIRED"
    assert world.actor.location_id == "room"


@pytest.mark.parametrize("size", ["medium", "large"])
@pytest.mark.parametrize("exit_id", ["window", "drain"])
def test_larger_bodies_cannot_fit_even_with_flight_and_swimming(world, size, exit_id):
    world.actor.body = Body(description="A larger flying swimmer.", size=size,
                            capabilities=["move", "manipulate", "fly", "swim"], capacity=10)
    go = next(action for action in world.actions() if action["id"] == "go")
    assert "cannot fit" in go["target_requirements"][exit_id]
    result = act(world, "go", target=exit_id)
    assert result["code"] == "BODY_SIZE_REQUIRED"
    assert size in result["explanation"]
    assert world.actor.location_id == "room"
    assert world.actor.action_clock == 0
    assert world.evaluate_goal("actor_outside_start_location")["status"] == "active"


def test_observations_and_reload_preserve_route_size_limits(world):
    observation = world.observe()
    assert observation["self"]["body"]["size"] == "medium"
    for exit_id in ["window", "drain"]:
        visible = next(entity for entity in observation["visible_entities"] if entity["id"] == exit_id)
        assert visible["max_body_size"] == "small"
        assert "leads_to" not in visible
    world.actor.body.size = "small"
    world.save()
    reloaded = World(world.scenario, world.path, "world", "npc-a", "session-a")
    assert reloaded.actor.body.size == "small"
    assert reloaded.state.entities["window"].max_body_size == "small"
    assert reloaded.state.entities["drain"].max_body_size == "small"


def test_size_without_an_ability_and_tools_cannot_bypass_the_restrictions(world):
    world.actor.body.size = "small"
    assert act(world, "take", target="rock")["outcome"] == "APPLIED"
    for exit_id in ["window", "drain"]:
        assert act(world, "go", target=exit_id)["code"] == "BODY_CAPABILITY_REQUIRED"
        assert act(world, "hit", target=exit_id, item="rock")["code"] == "INVALID_ARGUMENTS"
    assert world.actor.location_id == "room"


def test_legacy_defaults_and_unrestricted_routes_preserve_the_door_solution(world):
    assert world.actor.body.size == "medium"
    assert world.state.entities["door"].max_body_size is None
    world.actor.body.size = "large"
    for action, args in [
        ("open", {"target": "box"}), ("take", {"target": "key"}),
        ("unlock", {"target": "door", "item": "key"}), ("open", {"target": "door"}),
        ("go", {"target": "door"}),
    ]:
        assert act(world, action, **args)["outcome"] == "APPLIED"
    assert world.evaluate_goal("actor_outside_start_location")["status"] == "achieved"


def test_invalid_size_categories_are_not_silently_accepted():
    with pytest.raises(ValidationError):
        Body.model_validate({"description": "Body", "capabilities": [], "capacity": 1, "size": "tiny"})
    with pytest.raises(ValidationError):
        Entity.model_validate({"name": "Route", "kind": "door", "description": "Route", "max_body_size": "huge"})
