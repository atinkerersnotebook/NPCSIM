from uuid import uuid4

import pytest

from npc_sim.models import Entity, WorldState
from npc_sim.storage import read_json
from test_world import act, world


def test_takability_and_carried_box_contents(world):
    actions = world.observe()["actions"]
    take = next(action for action in actions if action["id"] == "take")
    assert set(take["targets"]) == {"box", "rock"}
    assert act(world, "take", target="door")["outcome"] == "REJECTED"
    assert act(world, "take", target="lock")["outcome"] == "REJECTED"
    assert act(world, "take", target="box")["outcome"] == "APPLIED"
    assert "key" not in world.visible_ids()
    assert act(world, "open", target="box")["outcome"] == "APPLIED"
    assert "key" in world.visible_ids()
    assert act(world, "take", target="key")["code"] == "CAPACITY_EXCEEDED"
    act(world, "drop", target="box")
    assert act(world, "take", target="key")["outcome"] == "APPLIED"


def test_smashing_box_reveals_key_and_creates_debris_exactly_once(world):
    act(world, "look", target="door")
    act(world, "take", target="rock")
    first = act(world, "hit", target="box", item="rock")
    assert first["code"] == "DAMAGED"
    assert world.state.entities["box"].health == 25
    assert world.state.entities["rock"].health == 95
    assert "5 damage to the rock" in first["explanation"]
    assert "key" not in world.visible_ids()
    request = (uuid4().hex, world.revision(), "hit", {"target": "box", "item": "rock"})
    broken = world.act(*request)
    assert broken["code"] == "BROKEN"
    assert world.state.entities["box"].health == 0
    assert world.state.entities["rock"].health == 90
    assert {"key", "box-splinters"} <= set(world.visible_ids())
    assert world.state.entities["key"].location == "box"
    clock = world.actor.action_clock
    assert world.act(*request) == broken
    assert world.actor.action_clock == clock
    assert world.state.entities["rock"].health == 90
    assert act(world, "hit", target="box", item="rock")["outcome"] == "NO_CHANGE"
    assert len([eid for eid in world.state.entities if eid == "box-splinters"]) == 1
    act(world, "drop", target="rock")
    act(world, "take", target="key")
    assert act(world, "unlock", target="lock", item="key")["outcome"] == "APPLIED"
    assert not world.state.entities["door"].locked
    act(world, "open", target="door")
    act(world, "go", target="door")
    assert world.evaluate_goal(world.scenario.goal_type)["status"] == "achieved"


def test_lock_damage_never_opens_door_and_becomes_irreversible(world):
    act(world, "look", target="door")
    act(world, "take", target="rock")
    for health in (50, 25, 0):
        result = act(world, "hit", target="lock", item="rock")
        assert result["outcome"] == "APPLIED"
        assert world.state.entities["lock"].health == health
        assert world.state.entities["door"].locked
        assert not world.state.entities["door"].open
    assert world.evaluate_goal(world.scenario.goal_type)["status"] == "failed"
    act(world, "drop", target="rock")
    act(world, "open", target="box")
    act(world, "take", target="key")
    assert act(world, "unlock", target="door", item="key")["code"] == "LOCK_BROKEN"
    assert act(world, "open", target="door")["code"] == "LOCK_BROKEN"
    assert act(world, "go", target="door")["code"] == "EXIT_BROKEN"
    loaded = WorldState.model_validate(read_json(world.path))
    assert loaded.entities["lock"].health == 0


def test_invalid_hits_do_not_damage_or_tick_needs(world):
    initial = world.state.entities["box"].health
    assert act(world, "hit", target="box", item="rock")["code"] == "NOT_CARRIED"
    act(world, "take", target="rock")
    clock = world.actor.action_clock
    assert act(world, "hit", target="rock", item="rock")["code"] == "INVALID_TARGET"
    world.actor.body.capabilities.remove("manipulate")
    assert act(world, "hit", target="box", item="rock")["code"] == "BODY_CAPABILITY_REQUIRED"
    assert world.actor.action_clock == clock
    assert world.state.entities["box"].health == initial


def test_intact_opening_does_not_damage_box_or_spawn_debris(world):
    assert act(world, "open", target="box")["outcome"] == "APPLIED"
    assert world.state.entities["box"].health == 50
    assert "key" in world.visible_ids()
    assert "box-splinters" not in world.state.entities


def test_old_portable_state_can_still_be_read(world):
    saved = world.state.model_dump(mode="json")
    saved["entities"]["rock"]["portable"] = saved["entities"]["rock"].pop("takable")
    loaded = WorldState.model_validate(saved)
    assert loaded.entities["rock"].takable
    assert "portable" not in loaded.model_dump(mode="json")["entities"]["rock"]


def test_both_objects_break_in_one_impact_and_spawn_once(world):
    initial_count = len(world.state.entities)
    act(world, "take", target="rock")
    box = world.state.entities["box"]
    rock = world.state.entities["rock"]
    box.health, box.damage, rock.health = 25, 25, 10
    rock.broken_name = "Cracked rock"
    rock.break_into = {"chips": Entity(name="Rock chips", kind="item", description="Small stone chips.", takable=True)}
    request = (uuid4().hex, world.revision(), "hit", {"target": "box", "item": "rock"})
    clock = world.actor.action_clock
    result = world.act(*request)
    assert result["code"] == "BROKEN"
    assert box.health == rock.health == 0
    assert rock.name == "Cracked rock"
    assert "25 damage" in result["explanation"]
    assert "10 damage to the rock" in result["explanation"]
    assert {"key", "box-splinters", "rock-chips"} <= set(world.visible_ids())
    assert world.state.entities["rock-chips"].location == world.actor.location_id
    assert world.actor.inventory == ["rock"]
    assert world.actor.action_clock == clock + 1
    assert world.act(*request) == result
    assert len(world.state.entities) == initial_count + 2
    assert act(world, "hit", target="door", item="rock")["code"] == "ITEM_BROKEN"
    assert WorldState.model_validate(read_json(world.path)).entities["rock"].health == 0


def test_zero_damage_tool_can_still_receive_damage(world):
    act(world, "take", target="rock")
    world.state.entities["rock"].damage = 0
    result = act(world, "hit", target="box", item="rock")
    assert result["outcome"] == "APPLIED"
    assert world.state.entities["box"].health == 50
    assert world.state.entities["rock"].health == 95
    world.state.entities["box"].damage = 0
    clock = world.actor.action_clock
    assert act(world, "hit", target="box", item="rock")["outcome"] == "NO_CHANGE"
    assert world.actor.action_clock == clock


def test_indestructible_tool_does_not_lose_health(world):
    act(world, "take", target="rock")
    world.state.entities["rock"].health = None
    world.state.entities["rock"].max_health = None
    assert act(world, "hit", target="box", item="rock")["outcome"] == "APPLIED"
    assert world.state.entities["box"].health == 25
    assert world.state.entities["rock"].health is None
