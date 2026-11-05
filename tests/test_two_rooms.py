from pathlib import Path
import json

import pytest

from npc_sim.models import Scenario
from npc_sim.storage import read_json
from npc_sim.world import World
from test_world import act


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def world(tmp_path):
    scenario = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "two-locked-rooms.json"))
    return World(scenario, tmp_path / "world-state.json", "world", "npc", "session")


def reveal_keys(world):
    act(world, "take", target="rock")
    act(world, "hit", target="box", item="rock")
    act(world, "hit", target="box", item="rock")
    act(world, "drop", target="rock")


def test_hidden_keys_and_locks_jammed_box_becomes_broken_box(world):
    assert world.scenario.objective == "Find a way out."
    initial = world.observe()
    assert initial["opening"] is True
    assert "no memory of how you got here" in initial["narrative"]
    assert not any(spoiler in json.dumps(initial) for spoiler in ("room-two", "door-two", "Second", "First", "both rooms"))
    assert set(world.visible_ids()) == {"door-one", "box", "rock"}
    assert "lock-one" not in world.visible_ids()
    assert act(world, "open", target="door-one")["code"] == "LOCKED"
    assert "lock-one" not in world.visible_ids()
    assert act(world, "hit", target="lock-one", item="rock")["outcome"] == "REJECTED"
    assert act(world, "look", target="door-one")["code"] == "INSPECTED"
    assert "lock-one" in world.visible_ids()
    assert act(world, "open", target="box")["code"] == "JAMMED"
    assert not {"silver-key", "brass-key"} & set(world.visible_ids())
    entity_ids = set(world.state.entities)
    reveal_keys(world)
    assert world.state.entities["box"].name == "Broken box"
    assert world.state.entities["box"].health == 0
    assert set(world.state.entities) == entity_ids
    assert {"silver-key", "brass-key"} <= set(world.visible_ids())
    assert all(world.state.entities[key].location == "box" for key in ("silver-key", "brass-key"))


def test_keys_are_not_interchangeable_and_backtracking_is_supported(world):
    reveal_keys(world)
    act(world, "take", target="brass-key")
    assert act(world, "unlock", target="door-one", item="brass-key")["code"] == "WRONG_ITEM"
    act(world, "drop", target="brass-key")
    act(world, "take", target="silver-key")
    assert act(world, "unlock", target="door-one", item="silver-key")["outcome"] == "APPLIED"
    act(world, "open", target="door-one")
    assert "room-two" not in json.dumps(world.observe())
    assert "leads_to" not in next(entity for entity in world.observe()["visible_entities"] if entity["id"] == "door-one")
    act(world, "go", target="door-one")
    assert world.actor.location_id == "room-two"
    assert world.evaluate_goal(world.scenario.goal_type)["status"] == "active"
    assert world.actor.visited_locations == ["room-one", "room-two"]
    assert world.observe()["opening"] is False
    assert next(entity for entity in world.observe()["visible_entities"] if entity["id"] == "door-one")["leads_to"] == "room-one"
    assert {"door-one", "door-two"} <= set(world.visible_ids())
    assert "box" not in world.visible_ids()
    assert "lock-two" not in world.visible_ids()
    clock = world.actor.action_clock
    assert act(world, "take", target="brass-key")["outcome"] == "REJECTED"
    assert world.actor.action_clock == clock
    assert world.state.entities["brass-key"].location == "room-one"
    act(world, "look", target="door-two")
    assert "lock-two" in world.visible_ids()
    assert act(world, "unlock", target="lock-two", item="silver-key")["code"] == "WRONG_ITEM"
    act(world, "go", target="door-one")
    assert world.actor.location_id == "room-one"
    assert "wake up" not in world.observe()["narrative"]
    act(world, "drop", target="silver-key")
    act(world, "take", target="brass-key")
    act(world, "go", target="door-one")
    assert world.actor.location_id == "room-two"
    assert act(world, "unlock", target="lock-two", item="brass-key")["outcome"] == "APPLIED"
    act(world, "open", target="door-two")
    act(world, "go", target="door-two")
    assert world.actor.location_id == "outside"
    assert world.evaluate_goal(world.scenario.goal_type)["status"] == "achieved"


def test_opening_is_actor_local_and_does_not_repeat_after_inspection(world):
    act(world, "look", target="door-one")
    assert world.observe()["opening"] is False
    other = world.bind("other", "other-session")
    assert other.observe()["opening"] is True
    assert other.actor.visited_locations == ["room-one"]
    assert "lock-one" not in other.visible_ids()


def test_first_broken_lock_blocks_route_even_when_final_door_is_intact(world):
    act(world, "look", target="door-one")
    act(world, "take", target="rock")
    for _ in range(3):
        act(world, "hit", target="lock-one", item="rock")
    assert world.state.entities["lock-two"].health == 75
    assert world.evaluate_goal(world.scenario.goal_type)["status"] == "failed"


def test_second_broken_lock_blocks_route_from_first_room(world):
    world.state.entities["lock-two"].health = 0
    assert world.evaluate_goal(world.scenario.goal_type)["status"] == "failed"


def test_carried_broken_box_releases_visibility_without_freeing_capacity(world):
    reveal_keys(world)
    act(world, "take", target="box")
    assert world.actor.inventory == ["box"]
    assert {"silver-key", "brass-key"} <= set(world.visible_ids())
    assert act(world, "take", target="silver-key")["code"] == "CAPACITY_EXCEEDED"
    act(world, "drop", target="box")
    assert act(world, "take", target="silver-key")["outcome"] == "APPLIED"
