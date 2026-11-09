from pathlib import Path

import pytest

from npc_sim.models import Scenario
from npc_sim.storage import read_json, write_json
from npc_sim.world import World
from npc_sim.world_storage import WorldStorage, read_world_state


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def world(tmp_path):
    scenario = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "escape-room.json"))
    return World(scenario, tmp_path / "world-state.json", "world", "npc-a", "session-a")


def test_checkpoint_stays_small_and_old_retries_survive_restart(world):
    first_request = ("first", world.revision(), "open", {"target": "box"})
    first_result = world.act(*first_request)
    for index in range(10):
        world.act(f"early-{index}", world.revision(), "unknown", {})
    initial_size = world.path.stat().st_size
    for index in range(120):
        world.act(f"later-{index}", world.revision(), "unknown", {})
    assert world.path.stat().st_size < initial_size + 12_000
    assert len(read_json(world.path)["requests"]) <= 1
    assert len(world.state.requests) <= 1
    restored = World(world.scenario, world.path, "world", "npc-a", "session-a")
    before = restored.snapshot()
    assert restored.act(*first_request) == first_result
    assert restored.snapshot() == before
    assert restored.act("first", first_request[1], "close", {"target": "box"})["code"] == "REQUEST_ID_CONFLICT"


def test_legacy_inline_history_is_migrated_without_changing_the_world(world):
    request = ("legacy", world.revision(), "open", {"target": "box"})
    result = world.act(*request)
    legacy = world.state.model_dump(mode="json")
    legacy["requests"] = {
        f"session-a:old-{index}": {"request": {"index": index}, "result": result}
        for index in range(100)
    }
    legacy["requests"]["session-a:legacy"] = {
        "request": {"action_id": request[2], "arguments": request[3],
                    "expected_observation_revision": request[1]},
        "result": result,
    }
    write_json(world.path, legacy)
    expected = world.snapshot()
    restored = World(world.scenario, world.path, "world", "npc-a", "session-a")
    assert restored.snapshot() == expected
    assert len(read_json(world.path)["requests"]) <= 1
    assert restored.act(*request) == result


def test_action_observation_cannot_commit_before_its_retry_result(world, monkeypatch):
    world.observe()
    previous = read_json(world.path)
    record = world._record

    def check_commit(identity, request, result):
        assert read_json(world.path) == previous
        return record(identity, request, result)

    monkeypatch.setattr(world, "_record", check_commit)
    result = world.act("open", world.revision(), "open", {"target": "box"})
    assert result["outcome"] == "APPLIED"
    assert read_json(world.path)["requests"]["session-a:open"]["result"] == result


def test_failed_checkpoint_after_archiving_never_records_an_uncommitted_action(world, monkeypatch):
    request = ("open", world.revision(), "open", {"target": "box"})
    opened = world.act(*request)
    before = world.snapshot()
    persisted = read_json(world.path)
    take = ("take", world.revision(), "take", {"target": "key"})

    def fail(*args):
        raise OSError("Checkpoint disk unavailable")

    with monkeypatch.context() as patch:
        patch.setattr("npc_sim.world_storage.write_json", fail)
        with pytest.raises(OSError, match="Checkpoint disk unavailable"):
            world.act(*take)
    assert world.snapshot() == before
    assert read_json(world.path) == persisted
    restored = World(world.scenario, world.path, "world", "npc-a", "session-a")
    assert restored.act(*request) == opened
    taken = restored.act(*take)
    assert taken["outcome"] == "APPLIED"
    assert restored.act(*take) == taken
    assert restored.actor.inventory == ["key"]
    assert len(restored.export_state().requests) == 2


def test_archive_failure_preserves_checkpoint_and_in_memory_world(world, monkeypatch):
    world.act("open", world.revision(), "open", {"target": "box"})
    before = world.snapshot()
    persisted = read_json(world.path)

    def fail(*args):
        raise OSError("Retry archive disk unavailable")

    monkeypatch.setattr(WorldStorage, "_archive", fail)
    with pytest.raises(OSError, match="Retry archive disk unavailable"):
        world.act("take", world.revision(), "take", {"target": "key"})
    assert world.snapshot() == before
    assert read_json(world.path) == persisted


def test_portable_export_contains_all_history_and_reimports_without_a_database(world, tmp_path):
    requests = []
    for index in range(4):
        request = (f"request-{index}", world.revision(), "unknown", {})
        requests.append((request, world.act(*request)))
    exported = read_world_state(world.path, include_history=True)
    assert exported.request_history_id is None
    assert len(exported.requests) == 4
    destination = tmp_path / "restored" / "world-state.json"
    write_json(destination, exported.model_dump(mode="json"))
    restored = World(world.scenario, destination, "world", "npc-a", "session-a")
    for request, result in requests:
        assert restored.act(*request) == result
    assert restored.snapshot() == world.snapshot()


@pytest.mark.parametrize("damage", ["missing", "foreign", "token"])
def test_missing_or_mismatched_retry_history_is_an_explicit_error(world, tmp_path, damage):
    world.act("first", world.revision(), "unknown", {})
    world.act("second", world.revision(), "unknown", {})
    storage = WorldStorage(world.path)
    if damage == "missing":
        storage.history_path.unlink()
    elif damage == "foreign":
        other_path = tmp_path / "other" / "world-state.json"
        other = World(world.scenario, other_path, "other-world", "npc-a", "session-a")
        other.act("first", other.revision(), "unknown", {})
        other.act("second", other.revision(), "unknown", {})
        storage.history_path.write_bytes(WorldStorage(other_path).history_path.read_bytes())
    else:
        checkpoint = read_json(world.path)
        checkpoint["request_history_id"] = "0" * 32
        write_json(world.path, checkpoint)
    with pytest.raises((OSError, ValueError), match="retry history"):
        World(world.scenario, world.path, "world", "npc-a", "session-a")


def test_rejected_action_write_failure_does_not_leave_an_in_memory_retry_result(world, monkeypatch):
    world.observe()
    before = world.state.requests.copy()

    def fail(*args):
        raise OSError("Checkpoint disk unavailable")

    monkeypatch.setattr("npc_sim.world_storage.write_json", fail)
    with pytest.raises(OSError, match="Checkpoint disk unavailable"):
        world.act("reject", world.revision(), "unknown", {})
    assert world.state.requests == before


def test_archived_operator_intervention_replays_and_conflicts_are_still_rejected(world):
    from npc_sim.interactions import apply_edit
    from test_interactions import nook_plan

    revision = world.revision()
    plan = nook_plan()
    result = apply_edit(world, "edit", revision, plan)
    world.act("next", world.revision(), "unknown", {})
    restored = World(world.scenario, world.path, "world", "npc-a", "session-a")
    before = restored.snapshot()
    assert apply_edit(restored, "edit", revision, plan) == result
    assert restored.snapshot() == before
    changed = plan.model_copy(deep=True)
    changed.summary = "Different plan"
    with pytest.raises(ValueError, match="reused"):
        apply_edit(restored, "edit", revision, changed)


def test_history_backup_is_independently_restorable(world, tmp_path):
    request = ("first", world.revision(), "open", {"target": "box"})
    result = world.act(*request)
    world.act("second", world.revision(), "unknown", {})
    destination = tmp_path / "backup" / "original-world-state.json"
    destination.parent.mkdir()
    destination.write_bytes(world.path.read_bytes())
    WorldStorage(world.path).backup_history(world.state, destination)
    restored = World(world.scenario, destination, "world", "npc-a", "session-a")
    assert restored.act(*request) == result
    assert restored.snapshot() == world.snapshot()


def test_callers_cannot_mutate_the_durable_retry_result(world):
    arguments = {"target": "box"}
    revision = world.revision()
    result = world.act("first", revision, "open", arguments)
    original = read_json(world.path)["requests"]["session-a:first"]["result"]
    arguments["target"] = "door"
    result["explanation"] = "Caller changed the response"
    world.act("next", world.revision(), "unknown", {})
    restored = World(world.scenario, world.path, "world", "npc-a", "session-a")
    assert restored.act("first", revision, "open", {"target": "box"}) == original


def test_interrupted_legacy_migration_preserves_original_and_can_be_retried(world, monkeypatch):
    result = world.act("first", world.revision(), "unknown", {})
    legacy = world.state.model_dump(mode="json")
    legacy["requests"].update({
        f"session-a:old-{index}": {"request": {"index": index}, "result": result}
        for index in range(10)
    })
    write_json(world.path, legacy)
    original = world.path.read_bytes()

    def fail(*args):
        raise OSError("Checkpoint disk unavailable")

    with monkeypatch.context() as patch:
        patch.setattr("npc_sim.world_storage.write_json", fail)
        with pytest.raises(OSError, match="Checkpoint disk unavailable"):
            World(world.scenario, world.path, "world", "npc-a", "session-a")
    assert world.path.read_bytes() == original
    restored = World(world.scenario, world.path, "world", "npc-a", "session-a")
    assert restored.export_state().requests == legacy["requests"]
    assert len(restored.state.requests) == 1
