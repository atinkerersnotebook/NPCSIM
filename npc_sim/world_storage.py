import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Generator
from uuid import uuid4

from npc_sim.models import WorldState
from npc_sim.storage import read_json, write_json


class WorldStorage:
    def __init__(self, path: Path):
        self.path = path
        self.history_path = path.with_suffix(".requests.sqlite3")
        self._committed_requests: dict[str, dict[str, Any]] = {}

    @contextmanager
    def _connect(self, *, create: bool = False) -> Generator[sqlite3.Connection, None, None]:
        mode = "rwc" if create else "ro"
        try:
            with closing(sqlite3.connect(
                    self.history_path.resolve().as_uri() + f"?mode={mode}", uri=True)) as connection:
                connection.execute("PRAGMA synchronous=FULL")
                with connection:
                    yield connection
        except sqlite3.Error as error:
            raise OSError(f"Cannot access world retry history {self.history_path}: {error}") from error

    @staticmethod
    def _validate(connection: sqlite3.Connection, state: WorldState) -> str:
        row = connection.execute(
            "SELECT world_id, scenario_id, history_id FROM metadata WHERE id = 1").fetchone()
        if (row is None or row[:2] != (state.world_id, state.scenario_id)
                or (state.request_history_id is not None and row[2] != state.request_history_id)):
            raise ValueError("World retry history does not match the saved checkpoint.")
        return row[2]

    def load(self) -> WorldState:
        state = WorldState.model_validate(read_json(self.path))
        if state.request_history_id is not None:
            with self._connect() as connection:
                self._validate(connection, state)
        self._committed_requests = state.requests.copy()
        return state

    def _archive(self, state: WorldState, entries: dict[str, dict[str, Any]]) -> str:
        with self._connect(create=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS metadata (id INTEGER PRIMARY KEY CHECK(id = 1), "
                "world_id TEXT NOT NULL, scenario_id TEXT NOT NULL, history_id TEXT NOT NULL)")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS requests (identity TEXT PRIMARY KEY, record TEXT NOT NULL)")
            if connection.execute("SELECT id FROM metadata WHERE id = 1").fetchone() is None:
                if state.request_history_id is not None:
                    raise ValueError("The checkpoint's world retry history is missing.")
                connection.execute("INSERT INTO metadata VALUES (1, ?, ?, ?)",
                                   (state.world_id, state.scenario_id, uuid4().hex))
            history_id = self._validate(connection, state)
            for identity, entry in entries.items():
                previous = connection.execute(
                    "SELECT record FROM requests WHERE identity = ?", (identity,)).fetchone()
                if previous is not None:
                    if json.loads(previous[0]) != entry:
                        raise ValueError("A persisted retry result cannot be replaced with a different result.")
                else:
                    connection.execute("INSERT INTO requests VALUES (?, ?)", (
                        identity, json.dumps(entry, ensure_ascii=True, allow_nan=False, separators=(",", ":"))))
        return history_id

    def save(self, state: WorldState) -> None:
        requests = list(state.requests.items())
        retained = dict(requests[-1:])
        older = dict(requests[:-1])
        if any(self._committed_requests.get(key) != value for key, value in older.items()):
            raise ValueError("Retry results must be checkpointed before they can be archived.")
        history_id = state.request_history_id
        # Archive only previously committed results; the new result and state commit together.
        if older:
            history_id = self._archive(state, older)
        document = state.model_dump(mode="json", exclude={"requests", "request_history_id"})
        document.update(requests=retained, request_history_id=history_id)
        write_json(self.path, document)
        state.requests = retained
        state.request_history_id = history_id
        self._committed_requests = retained.copy()

    def get_request(self, state: WorldState, identity: str) -> dict[str, Any] | None:
        if identity in state.requests:
            return state.requests[identity]
        if state.request_history_id is None:
            return None
        with self._connect() as connection:
            self._validate(connection, state)
            row = connection.execute("SELECT record FROM requests WHERE identity = ?", (identity,)).fetchone()
        return json.loads(row[0]) if row is not None else None

    def export(self, state: WorldState) -> WorldState:
        result = state.model_copy(deep=True)
        if state.request_history_id is not None:
            with self._connect() as connection:
                self._validate(connection, state)
                history = {identity: json.loads(record) for identity, record in
                           connection.execute("SELECT identity, record FROM requests")}
            result.requests = {**history, **result.requests}
        result.request_history_id = None
        return result

    def backup_history(self, state: WorldState, checkpoint: Path) -> None:
        if state.request_history_id is not None:
            destination = WorldStorage(checkpoint)
            with self._connect() as source:
                self._validate(source, state)
                with closing(sqlite3.connect(destination.history_path)) as target:
                    source.backup(target)


def read_world_state(path: Path, *, include_history: bool = False) -> WorldState:
    storage = WorldStorage(path)
    state = storage.load()
    return storage.export(state) if include_history else state
