import json
from unittest.mock import Mock

from npc_sim.self_heal import Supervisor


def supervisor(tmp_path):
    return Supervisor(root=tmp_path, data=tmp_path, server_command=["server"],
                      repair_command=["agent"], test_command=["pytest"], checkpoint_interval=0)


def test_recover_reopens_confirmed_boundary(tmp_path):
    guard = supervisor(tmp_path)
    guard.request = Mock(side_effect=[{"run": {"id": "episode", "status": "error", "reason": "failure"}},
                                      {}, {}])
    guard.recover()
    assert guard.request.call_args_list[1].args[0] == "/api/adventure/reopen"
    assert guard.request.call_args_list[2].args[0] == "/api/control/resume"


def test_recover_uses_latest_valid_save(tmp_path):
    from urllib.error import HTTPError

    guard = supervisor(tmp_path)
    saved = Mock(id="checkpoint", resumable=True, saved_at="2026-01-01", run={"id": "episode"})
    guard.request = Mock(side_effect=[{"run": {"id": "episode", "status": "error", "reason": "failure"}},
                                      HTTPError("url", 409, "not reopenable", {}, None), {}, {}])
    (tmp_path / "saves").mkdir()
    (tmp_path / "saves" / "checkpoint.json").write_text(json.dumps({}))
    from unittest.mock import patch
    with patch("npc_sim.self_heal.read_save", return_value=saved):
        guard.recover()
    assert guard.request.call_args_list[2].args == (
        "/api/saves/load", {"save_id": "checkpoint", "current_run_id": "episode"})


def test_checkpoint_pauses_and_resumes_only_resumable(tmp_path):
    guard = supervisor(tmp_path)
    guard.request = Mock(return_value={"save": {"resumable": True}})
    guard.checkpoint({"id": "episode", "status": "running"})
    assert guard.request.call_count == 2
    assert guard.request.call_args.args[0] == "/api/control/resume"
    guard.request.reset_mock()
    guard.request.return_value = {"save": {"resumable": False}}
    guard.checkpoint({"id": "episode", "status": "running"})
    assert guard.request.call_count == 1


def test_repair_refuses_dirty_source(tmp_path):
    guard = supervisor(tmp_path)
    guard._code_changes = Mock(return_value=" M npc_sim/world.py")
    assert guard.repair("broken") is False


def test_repair_applies_and_validates_fix(tmp_path):
    from unittest.mock import patch

    guard = supervisor(tmp_path)
    guard._code_changes = Mock(side_effect=["", " M npc_sim/world.py"])
    with patch("npc_sim.self_heal.subprocess.run") as run:
        run.side_effect = [Mock(returncode=0, stdout="patched"), Mock(returncode=0, stdout="4 passed")]
        assert guard.repair("failure") is True
    assert run.call_count == 2
    assert run.call_args_list[0].kwargs["input"].endswith("failure")
    assert run.call_args_list[1].args[0] == ["pytest"]
