import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from npc_sim.contracts import observation
from npc_sim.models import Entity, Scenario
from npc_sim.storage import EventStore, read_json, write_json
from npc_sim.world import World


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def world(tmp_path):
    scenario = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "escape-room.json"))
    return World(scenario, tmp_path / "world-state.json", "world", "npc-a", "session-a")


def act(world, action, **arguments):
    return world.act(uuid4().hex, world.revision(), action, arguments)


def test_hidden_key_and_schemas(world):
    visible = observation(world.observe())
    assert "key" not in [entity["id"] for entity in visible["visible_entities"]]
    assert "key_id" not in json.dumps(visible)
    assert world.snapshot()["entities"]["key"]["location"] == "box"
    for action in visible["actions"]:
        Draft202012Validator.check_schema(action["arguments_schema"])
    assert act(world, "look", target="box")["outcome"] == "APPLIED"
    assert "key" not in [entity["id"] for entity in world.observe()["visible_entities"]]


def test_escape_rules_and_deduplication(world):
    physical = {eid: entity.model_dump() for eid, entity in world.state.entities.items()}
    assert act(world, "open", target="door")["code"] == "LOCKED"
    assert {eid: entity.model_dump() for eid, entity in world.state.entities.items()} == physical
    assert world.actor.action_clock == 0
    assert act(world, "unlock", target="door", item="key")["code"] == "NOT_CARRIED"
    assert act(world, "go", target="door")["outcome"] == "REJECTED"
    assert act(world, "open", target="box")["outcome"] == "APPLIED"
    assert act(world, "open", target="box")["outcome"] == "NO_CHANGE"
    request = ("take-key", world.revision(), "take", {"target": "key"})
    result = world.act(*request)
    assert result["outcome"] == "APPLIED"
    clock = world.actor.action_clock
    assert world.act(*request) == result
    assert world.actor.action_clock == clock
    assert world.act("take-key", request[1], "drop", {"target": "key"})["code"] == "REQUEST_ID_CONFLICT"
    assert act(world, "drop", target="key")["outcome"] == "APPLIED"
    assert world.actor.inventory == []
    assert world.state.entities["key"].location == "room"
    act(world, "take", target="key")
    assert act(world, "unlock", target="door", item="key")["outcome"] == "APPLIED"
    assert not world.state.entities["door"].open
    assert world.evaluate_goal("actor_outside_start_location")["status"] == "active"
    act(world, "open", target="door")
    assert world.evaluate_goal("actor_outside_start_location")["status"] == "active"
    act(world, "go", target="door")
    assert world.evaluate_goal("actor_outside_start_location")["status"] == "achieved"
    observation(world.observe())
    for action in world.observe()["actions"]:
        Draft202012Validator.check_schema(action["arguments_schema"])


def test_body_capacity_and_wrong_item(world):
    world.state.entities["stone"] = Entity(name="Stone", kind="item", description="A stone.",
                                           location="room", portable=True)
    act(world, "take", target="stone")
    act(world, "open", target="box")
    assert act(world, "take", target="key")["code"] == "CAPACITY_EXCEEDED"
    assert world.state.entities["key"].location == "box"
    assert act(world, "unlock", target="door", item="stone")["code"] == "WRONG_ITEM"
    act(world, "drop", target="stone")
    world.actor.body.capabilities.remove("manipulate")
    assert act(world, "take", target="key")["code"] == "BODY_CAPABILITY_REQUIRED"


def test_independent_self_competing_take_and_goal(world):
    other = world.bind("npc-b", "session-b")
    act(world, "open", target="box")
    shared_revision = world.revision()
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(world.act, "race-a", shared_revision, "take", {"target": "key"})
        second = executor.submit(other.act, "race-b", shared_revision, "take", {"target": "key"})
        results = [first.result(), second.result()]
    assert sum(result["outcome"] == "APPLIED" for result in results) == 1
    assert len(world.actor.inventory) + len(other.actor.inventory) == 1
    assert world.observe()["self"]["actor_id"] != other.observe()["self"]["actor_id"]
    world.actor.location_id = "outside"
    assert world.evaluate_goal("actor_outside_start_location")["status"] == "achieved"
    assert other.evaluate_goal("actor_outside_start_location")["status"] == "active"
    with pytest.raises(ValueError):
        world.bind("npc-a", "wrong-session")


def test_cooperative_messages_handoffs_and_team_verification(tmp_path):
    scenario = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "two-agent-escape.json"))
    scout = World(scenario, tmp_path / "world.json", "team-world", "scout", "scout-session")
    assert scout.evaluate_goal(scenario.goal_type)["status"] == "active"
    locksmith = scout.bind("locksmith", "locksmith-session")
    assert scout.observe()["visible_actors"] == [{
        "actor_id": "locksmith", "inventory": [], "capabilities": ["move", "manipulate"]}]
    assert "courtyard-key" not in json.dumps(scout.observe())
    assert act(scout, "say", message="   ")["code"] == "INVALID_ARGUMENTS"
    request = ("hello", scout.revision(), "say", {"message": "I found a box."})
    result = scout.act(*request)
    assert scout.act(*request) == result
    assert locksmith.observe()["messages"] == [{"actor_id": "scout", "text": "I found a box."}]
    assert act(scout, "open", target="supply-box")["outcome"] == "APPLIED"
    assert act(scout, "take", target="passage-key")["outcome"] == "APPLIED"
    locksmith.actor.body.capacity = 0
    assert act(scout, "give", target="passage-key", recipient="locksmith")["code"] == "CAPACITY_EXCEEDED"
    assert scout.actor.inventory == ["passage-key"]
    locksmith.actor.body.capacity = 1
    handoff = ("handoff", scout.revision(), "give", {"target": "passage-key", "recipient": "locksmith"})
    given = scout.act(*handoff)
    assert scout.act(*handoff) == given
    assert given["code"] == "GIVEN"
    assert scout.actor.inventory == []
    assert locksmith.actor.inventory == ["passage-key"]
    assert scout.state.entities["passage-key"].location == "locksmith"
    assert "passage-key" not in {entity["id"] for entity in scout.observe()["visible_entities"]}
    scout.actor.location_id = "courtyard"
    assert not scout.observe()["visible_actors"]
    assert scout.evaluate_goal(scenario.goal_type)["status"] == "active"
    assert act(scout, "say", message="Too far away.")["code"] == "ACTION_UNAVAILABLE"
    assert act(locksmith, "give", target="passage-key", recipient="scout")["code"] == "ACTION_UNAVAILABLE"
    locksmith.actor.location_id = "courtyard"
    assert scout.evaluate_goal(scenario.goal_type)["status"] == "achieved"
    with pytest.raises(ValueError, match="not declared"):
        scout.bind("intruder", "intruder-session")


def test_cooperative_roles_start_locations_and_message_limit(tmp_path):
    raw = read_json(ROOT / "data" / "scenarios" / "two-agent-escape.json")
    raw["agents"][1]["start_location"] = "hall"
    scenario = Scenario.model_validate(raw)
    scout = World(scenario, tmp_path / "world.json", "world", "scout", "s")
    locksmith = scout.bind("locksmith", "l")
    assert locksmith.actor.location_id == "hall"
    assert not scout.observe()["visible_actors"]
    locksmith.actor.location_id = "workroom"
    for index in range(25):
        assert act(scout, "say", message=f"Message {index}")["outcome"] == "APPLIED"
    assert len(locksmith.observe()["messages"]) == 20
    assert locksmith.observe()["messages"][0]["text"] == "Message 5"
    raw["agents"][1]["id"] = "scout"
    with pytest.raises(ValueError, match="unique"):
        Scenario.model_validate(raw)


def test_needs_progression_toggles_and_recovery(world):
    other = world.bind("npc-b", "session-b")
    act(world, "open", target="box")
    assert world.actor.needs["sleep"].level == 0
    world.toggle_need("npc-a", "sleep", True)
    act(world, "look", target="door")
    assert world.actor.needs["sleep"].level == 0
    act(world, "take", target="key")
    assert world.actor.needs["sleep"].level == 10
    assert other.actor.needs["sleep"].level == 0
    world.actor.needs["sleep"].level = 90
    act(world, "unlock", target="door", item="key")
    act(world, "open", target="door")
    clock = world.actor.action_clock
    assert act(world, "go", target="door")["code"] == "NEED_CONSTRAINT"
    assert world.actor.action_clock == clock
    assert act(world, "rest")["code"] == "RECOVERED"
    assert world.actor.needs["sleep"].level == 0
    world.toggle_need("npc-a", "sleep", False)
    world.actor.needs["sleep"].level = 70
    act(world, "drop", target="key")
    assert world.actor.needs["sleep"].level == 70
    world.toggle_need("npc-a", "sleep", True)
    assert world.actor.needs["sleep"].level == 70


def test_food_recovery_and_admin_command(world):
    world.state.entities["apple"] = Entity(name="Apple", kind="item", description="An edible apple.",
                                           location="room", portable=True, food=True)
    write_json(world.path.parent / "needs-command.json", {
        "schema_version": "1.0", "id": "toggle-1", "world_id": "world",
        "actor_id": "npc-a", "need": "food", "enabled": True})
    world.observe()
    revision = world.state.revision
    world.observe()
    assert world.state.revision == revision
    world.actor.needs["food"].level = 85
    act(world, "take", target="apple")
    assert act(world, "eat", target="apple")["outcome"] == "APPLIED"
    assert world.actor.needs["food"].level == 0
    assert world.actor.inventory == []


def test_stale_malformed_and_persisted_state(world):
    stale = world.revision()
    act(world, "open", target="box")
    assert world.act("stale", stale, "take", {"target": "key"})["code"] == "STALE_OBSERVATION"
    assert act(world, "take", target="nonexistent")["code"] == "INVALID_ARGUMENTS"
    with pytest.raises(ValueError):
        world.evaluate_goal("unsupported")
    loaded = World(world.scenario, world.path, "world", "npc-a", "session-a")
    assert loaded.state.model_dump() == world.state.model_dump()


def test_second_scenario_new_action_schema(tmp_path):
    scenario = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "sleepy-room.json"))
    world = World(scenario, tmp_path / "world-state.json", "world", "traveler", "session")
    assert act(world, "go", target="archway")["code"] == "NEED_CONSTRAINT"
    assert act(world, "nap")["code"] == "INVALID_ARGUMENTS"
    assert act(world, "nap", duration=2)["outcome"] == "APPLIED"
    assert act(world, "go", target="archway")["outcome"] == "APPLIED"
    assert world.evaluate_goal(scenario.goal_type)["status"] == "achieved"


def test_event_rebuild_and_atomic_failure(tmp_path, monkeypatch):
    store = EventStore(tmp_path, "run")
    store.append({"kind": "example"})
    write_json(tmp_path / "transcript.json", {"broken": True})
    rebuilt = EventStore(tmp_path, "run")
    assert rebuilt.events == store.events
    assert read_json(tmp_path / "transcript.json")["events"] == ["run:1"]
    path = tmp_path / "value.json"
    write_json(path, {"value": "old"})
    import npc_sim.storage as storage
    def fail_replace(source, destination):
        raise OSError("simulated failure")
    monkeypatch.setattr(storage.os, "replace", fail_replace)
    with pytest.raises(OSError):
        write_json(path, {"value": "new"})
    assert read_json(path) == {"value": "old"}
    assert not list(tmp_path.glob("*.tmp"))
