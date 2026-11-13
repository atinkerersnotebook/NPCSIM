"""Opt-in supervisor for the local NPCSIM server and simulation."""

import argparse
import json
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from npc_sim.episode_saves import read_save
from npc_sim.storage import read_json

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent


class Supervisor:
    def __init__(self, *, root: Path, data: Path, server_command: list[str],
                 repair_command: list[str], test_command: list[str], port: int = 8000,
                 interval: float = 3, startup_timeout: float = 30, checkpoint_interval: float = 300):
        self.root, self.data = root, data
        self.server_command, self.repair_command = server_command, repair_command
        self.test_command = test_command
        self.port = port
        self.base = f"http://127.0.0.1:{port}"
        self.interval, self.startup_timeout = interval, startup_timeout
        self.checkpoint_interval = checkpoint_interval
        self._last_checkpoint = time.monotonic()
        self.server: subprocess.Popen | None = None
        self._handled_error: tuple[str, str] | None = None
        self._started_run: str | None = None
        self.log_path = data / "self-heal-server.log"
        self._log_stream = None

    def request(self, path: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload).encode() if payload is not None else None
        request = Request(self.base + path, data=body,
                          headers={"Content-Type": "application/json"} if body is not None else {},
                          method="POST" if body is not None else "GET")
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    def ready(self) -> bool:
        try:
            return self.request("/api/health").get("status") == "ok"
        except (URLError, TimeoutError, ValueError, OSError):
            return False

    def start(self) -> None:
        if self.server is not None and self.server.poll() is None:
            return
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", self.port)) == 0:
                raise FileExistsError("Another process already owns the configured port; stop it before supervising")
        pending = self.latest_run()
        self._started_run = pending["id"] if pending and pending.get("status") == "running" else None
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_stream = self.log_path.open("a", encoding="utf-8")
        self.server = subprocess.Popen(self.server_command, cwd=self.root,
                                       stdout=self._log_stream, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self.server.poll() is not None:
                raise RuntimeError(f"Server exited during startup ({self.server.returncode})")
            if self.ready():
                return
            time.sleep(0.25)
        raise RuntimeError("Server did not become healthy before the startup timeout")

    def stop(self) -> None:
        if self.server is not None and self.server.poll() is None:
            self.server.terminate()
            try:
                self.server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.server.kill()
                self.server.wait()
        self.server = None
        if self._log_stream is not None:
            self._log_stream.close()
            self._log_stream = None

    def _error_context(self, message: str) -> str:
        if self.log_path.exists():
            with self.log_path.open("rb") as stream:
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell() - 12000))
                message += "\nServer log:\n" + stream.read().decode("utf-8", errors="replace")
        return message

    def _code_changes(self) -> str:
        result = subprocess.run(["git", "status", "--porcelain", "--untracked-files=normal", "--", "npc_sim", "tests"],
                                cwd=self.root, capture_output=True, text=True, check=True)
        return result.stdout

    def repair(self, error: str) -> bool:
        if not self.repair_command:
            LOGGER.error("No repair agent configured; refusing unattended source changes")
            return False
        if self._code_changes():
            LOGGER.error("Source or tests have uncommitted changes; refusing to overwrite existing work")
            return False
        prompt = ("NPCSIM server or simulation stopped. Diagnose the error below and apply the smallest "
                  "source-code fix in this repository. Do not edit saved data, remove files, commit, or "
                  "restart the server. Add focused regression tests.\n\nError:\n" + error[-12000:])
        try:
            result = subprocess.run(self.repair_command, cwd=self.root, input=prompt, text=True,
                                    timeout=600, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            if self._code_changes():
                raise RuntimeError("Repair agent interrupted after changing source; inspection required") from exc
            LOGGER.error("Repair agent failed: %s", exc)
            return False
        LOGGER.info("Repair agent output: %s", result.stdout[-4000:])
        if result.returncode or not self._code_changes():
            if self._code_changes():
                raise RuntimeError("Repair agent failed after changing source; inspection required")
            LOGGER.error("Repair agent failed or made no source changes")
            return False
        try:
            test = subprocess.run(self.test_command, cwd=self.root, timeout=600,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("Repair validation could not finish; source changes require inspection") from exc
        if test.returncode:
            raise RuntimeError("Repair failed validation; source changes require inspection: " + test.stdout[-4000:])
        LOGGER.info("Repair passed validation")
        return True

    def latest_run(self) -> dict | None:
        paths = sorted((self.data / "runs").glob("*/run.json"), key=lambda path: path.stat().st_mtime_ns)
        return read_json(paths[-1]) if paths else None

    def recover(self) -> None:
        state = self.request("/api/state")
        run = state.get("run")
        if not run:
            return
        if run["status"] == "paused" and run.get("safe_checkpoint") and run["id"] == self._started_run:
            self.request("/api/control/resume", {})
            self._started_run = None
            return
        if run["status"] != "error":
            return
        identity = (run["id"], run.get("reason", ""))
        if identity == self._handled_error:
            return
        self._handled_error = identity
        try:
            self.request("/api/adventure/reopen", {"run_id": run["id"], "until_midnight": False})
        except HTTPError as exc:
            LOGGER.info("Verified reopen unavailable: %s", exc)
        else:
            self.request("/api/control/resume", {})
            LOGGER.warning("Reopened episode at its verified durable action boundary")
            return
        saves = []
        for path in (self.data / "saves").glob("*.json"):
            try:
                saved = read_save(self.data, path.stem)
                if saved.resumable and saved.run["id"] == run["id"]:
                    saves.append(saved)
            except (ValueError, OSError):
                LOGGER.warning("Skipping invalid save point %s", path.name)
        for saved in sorted(saves, key=lambda item: item.saved_at, reverse=True):
            save_id = saved.id
            try:
                self.request("/api/saves/load", {"save_id": save_id, "current_run_id": run["id"]})
            except HTTPError as exc:
                LOGGER.warning("Save %s could not be restored: %s", save_id, exc)
                continue
            self.request("/api/control/resume", {})
            LOGGER.warning("Restored episode from save %s", save_id)
            return
        LOGGER.warning("No usable safe save; restarting episode from beginning")
        self.request("/api/control/reset", {})
        self.request("/api/control/run", {})

    def checkpoint(self, run: dict) -> None:
        if run["status"] != "running" or time.monotonic() - self._last_checkpoint < self.checkpoint_interval:
            return
        self._last_checkpoint = time.monotonic()
        try:
            result = self.request("/api/saves", {"run_id": run["id"], "name": "Automatic recovery checkpoint"})
            if result["save"]["resumable"]:
                self.request("/api/control/resume", {})
            else:
                LOGGER.warning("Automatic checkpoint is not resumable; leaving episode paused")
        except (URLError, HTTPError, OSError, ValueError, KeyError) as exc:
            LOGGER.error("Could not create recovery checkpoint: %s", exc)

    def run(self) -> None:
        try:
            try:
                self.start()
            except RuntimeError as exc:
                self.stop()
                if not self.repair(self._error_context(str(exc))):
                    raise
                self.start()
            while True:
                time.sleep(self.interval)
                if self.server is None or self.server.poll() is not None or not self.ready():
                    error = f"Server exited ({self.server.poll() if self.server else 'unknown'}) or health check failed"
                    self.stop()
                    repaired = self.repair(self._error_context(error))
                    try:
                        self.start()
                    except RuntimeError:
                        if not repaired:
                            raise RuntimeError(error + "; repair unavailable and server could not restart")
                        raise
                    LOGGER.warning("Server restarted%s", " after verified repair" if repaired else " without code changes")
                try:
                    state = self.request("/api/state")
                    run = state.get("run")
                    if run and run["status"] == "error":
                        identity = (run["id"], run.get("reason", ""))
                        if identity != self._handled_error:
                            if self.repair(self._error_context(run.get("reason", "Unknown simulation error"))):
                                self.stop()
                                self.start()
                            self.recover()
                    elif run and run["status"] == "paused" and run.get("safe_checkpoint"):
                        self.recover()
                    elif run:
                        self.checkpoint(run)
                except (URLError, HTTPError, OSError, ValueError) as exc:
                    LOGGER.error("Recovery failed; leaving saved files untouched: %s", exc)
                    raise
        finally:
            self.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Supervise NPCSIM with an opt-in external code-repair agent")
    parser.add_argument("--data", type=Path, default=Path(os.environ.get("NPCSIM_DATA", ROOT / "data")))
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--interval", type=float, default=3)
    parser.add_argument("--repair-command", type=json.loads, required=True,
                        help='JSON argv array for an agent that reads its task on stdin and edits local source')
    parser.add_argument("--test-command", type=json.loads,
                        default=[sys.executable, "-m", "pytest", "-q", "tests"])
    args = parser.parse_args()
    if not isinstance(args.repair_command, list) or not args.repair_command or not all(
            isinstance(part, str) for part in args.repair_command):
        parser.error("--repair-command must be a nonempty JSON array of strings")
    if not isinstance(args.test_command, list) or not args.test_command or not all(
            isinstance(part, str) for part in args.test_command):
        parser.error("--test-command must be a nonempty JSON array of strings")
    logging.basicConfig(level=logging.INFO)
    supervisor = Supervisor(root=ROOT, data=args.data.resolve(),
                            server_command=[sys.executable, "-m", "uvicorn", "npc_sim.app:app",
                                            "--host", "127.0.0.1", "--port", str(args.port)],
                            repair_command=args.repair_command, test_command=args.test_command,
                            port=args.port, interval=args.interval)
    try:
        supervisor.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
