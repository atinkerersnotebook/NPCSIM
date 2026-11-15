import hashlib

import pytest

from npc_sim.storage import read_json, write_json
from npc_sim.village_release_evidence import capture_failure, create_baseline
from npc_sim.world import World
from test_village_expanded import scenario


def test_baseline_is_reproducible_and_does_not_change_scenario(tmp_path):
    source = tmp_path / "source.json"
    write_json(source, scenario().model_dump(mode="json"))
    before = source.read_bytes()
    first = create_baseline(source, tmp_path / "first")
    second = create_baseline(source, tmp_path / "second")
    assert source.read_bytes() == before
    assert first == second
    assert (tmp_path / "first" / "baseline.json").read_bytes() == (tmp_path / "second" / "baseline.json").read_bytes()
    assert first["scenario_sha256"] == hashlib.sha256(before).hexdigest()
    assert first["starting_state"]["minute"] == scenario().village.start_minute
    assert first["starting_state"]["stocks"] == scenario().village.stocks
    assert first["starting_state"]["orders"] == {}
    assert first["starting_state"]["meal_reservations"]["daily"]["expected"] == 12
    with pytest.raises(FileExistsError):
        create_baseline(source, tmp_path / "first")


def test_failure_capture_preserves_timestamped_state_and_inputs(tmp_path):
    source = tmp_path / "source.json"
    value = scenario()
    write_json(source, value.model_dump(mode="json"))
    baseline = tmp_path / "baseline"
    manifest = create_baseline(source, baseline)
    world_path = tmp_path / "failure-world.json"
    world_path.write_bytes((baseline / "baseline.json").read_bytes())
    world = World(value, world_path, "g8-baseline", value.agents[0].id,
                  f"baseline-{value.agents[0].id}")
    world.state.village.minute += 5
    world.state.village.stocks["tavern"]["bread"] = 1
    world.save()
    before = world_path.read_bytes()
    diagnostic = {"code": "NO_FOOD", "request_id": "case-1"}
    result = capture_failure(baseline, world_path, tmp_path / "failure.json", diagnostic)
    assert world_path.read_bytes() == before
    assert result["baseline_sha256"] == manifest["baseline_sha256"]
    assert result["state_sha256"] == hashlib.sha256(before).hexdigest()
    assert result["state"]["minute"] == value.village.start_minute + 5
    assert result["state"]["stocks"]["tavern"]["bread"] == 1
    assert result["state"]["meal_reservations"]["daily"]["prepared"] >= 1
    assert result["state"]["diagnostic"] == diagnostic
    assert read_json(tmp_path / "failure.json") == result
    with pytest.raises(FileExistsError):
        capture_failure(baseline, world_path, tmp_path / "failure.json", diagnostic)
    (baseline / "scenario.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        capture_failure(baseline, world_path, tmp_path / "other.json", diagnostic)
