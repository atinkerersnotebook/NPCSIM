"""Run an authenticated Copilot CLI repair from a supervisor stdin task."""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def command(root: Path, executable: str) -> list[str]:
    return [
        executable,
        "--no-auto-update",
        "--no-ask-user",
        "--disable-builtin-mcps",
        "--excluded-tools", "mcp:*",
        "--allow-tool", f"write({root / 'npc_sim' / '**'})",
        "--allow-tool", f"write({root / 'tests' / '**'})",
        "--prompt",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Adapt a supervisor stdin task to Copilot CLI prompt mode")
    parser.add_argument("--copilot", default=os.environ.get("NPCSIM_COPILOT_CLI") or
                        shutil.which("copilot") or str(Path.home() / ".local/bin/copilot"))
    args = parser.parse_args()
    prompt = sys.stdin.read()
    if not prompt.strip():
        parser.error("a repair task is required on standard input")
    try:
        result = subprocess.run(command(ROOT, args.copilot) + [prompt], cwd=ROOT, check=False)
    except OSError as exc:
        print(f"Copilot CLI unavailable: {exc}", file=sys.stderr)
        return 1
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
