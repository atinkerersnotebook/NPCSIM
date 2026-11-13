import subprocess
from unittest.mock import patch

from npc_sim import repair_agent


def test_command_scopes_unattended_edits(tmp_path):
    argv = repair_agent.command(tmp_path, "/usr/bin/copilot")
    assert argv[:2] == ["/usr/bin/copilot", "--no-auto-update"]
    assert "--no-ask-user" in argv
    assert "--disable-builtin-mcps" in argv
    assert "mcp:*" in argv
    assert f"write({tmp_path / 'npc_sim' / '**'})" in argv
    assert f"write({tmp_path / 'tests' / '**'})" in argv
    assert "--allow-all" not in argv
    assert argv[-1] == "--prompt"


def test_main_passes_stdin_as_prompt_and_exits_with_cli_status(monkeypatch):
    monkeypatch.setattr("sys.argv", ["repair_agent", "--copilot", "/usr/bin/copilot"])
    with patch("sys.stdin.read", return_value="fix the failure"), patch(
        "npc_sim.repair_agent.subprocess.run", return_value=subprocess.CompletedProcess([], 3)
    ) as run:
        assert repair_agent.main() == 3
    assert run.call_args.args[0][-2:] == ["--prompt", "fix the failure"]
    assert run.call_args.kwargs["cwd"] == repair_agent.ROOT


def test_main_fails_if_cli_missing(monkeypatch):
    monkeypatch.setattr("sys.argv", ["repair_agent", "--copilot", "/missing/copilot"])
    with patch("sys.stdin.read", return_value="failure"), patch(
        "npc_sim.repair_agent.subprocess.run", side_effect=FileNotFoundError("missing")
    ):
        assert repair_agent.main() == 1
