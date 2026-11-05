import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(6):
            try:
                os.replace(temporary, path)
                break
            except PermissionError as error:
                if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 5:
                    raise
                LOGGER.warning("File replacement temporarily blocked for %s; retry %s/5", path, attempt + 1)
                time.sleep(0.01 * 2 ** attempt)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class EventStore:
    def __init__(self, directory: Path, run_id: str):
        self.directory = directory
        self.run_id = run_id
        self.events: list[dict[str, Any]] = []
        for path in sorted((directory / "events").glob("*.json")):
            event = read_json(path)
            if event.get("schema_version") != "1.0" or event.get("run_id") != run_id:
                raise ValueError(f"Invalid event document: {path.name}")
            if event.get("sequence") != len(self.events) + 1:
                raise ValueError(f"Non-contiguous event sequence: {path.name}")
            self.events.append(event)
        self._index()

    def _index(self) -> None:
        write_json(self.directory / "transcript.json", {
            "schema_version": "1.0", "run_id": self.run_id,
            "events": [event["id"] for event in self.events],
        })

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        sequence = len(self.events) + 1
        entry = {**event, "schema_version": "1.0", "run_id": self.run_id,
                 "sequence": sequence, "id": f"{self.run_id}:{sequence}"}
        write_json(self.directory / "events" / f"{sequence:06}.json", entry)
        self.events.append(entry)
        self._index()
        return entry
