import json
import logging
import sqlite3
import zlib
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Generator
from uuid import uuid4

from npc_sim.models import WorldState
from npc_sim.storage import read_json, write_json


LOGGER = logging.getLogger(__name__)


class WorldStorage:
    def __init__(self, path: Path):
        self.path = path
        self.history_path = path.with_suffix(".requests.sqlite3")
        self.snapshot_path = path.with_suffix(".snapshots.sqlite3")
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
        try:
            self._record_snapshot(state)
        except (OSError, sqlite3.Error) as error:
            LOGGER.exception("World playback checkpoint could not be recorded: %s", error)

    def _snapshot_document(self, state: WorldState) -> bytes:
        snapshot = state.model_dump(mode="json", exclude={
            "requests", "admin_requests", "request_history_id"})
        raw = json.dumps(snapshot, ensure_ascii=True, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
        return zlib.compress(raw, level=6)

    def _connect_snapshots(self) -> sqlite3.Connection:
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.snapshot_path, timeout=10)
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS snapshots ("
            "sequence INTEGER PRIMARY KEY AUTOINCREMENT, revision INTEGER NOT NULL, "
            "minute INTEGER, state BLOB NOT NULL)")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(snapshots)")}
        if "turn" not in columns:
            connection.execute("ALTER TABLE snapshots ADD COLUMN turn INTEGER")
        if "turn_start" not in columns:
            connection.execute("ALTER TABLE snapshots ADD COLUMN turn_start INTEGER")
        return connection

    def _connect_snapshot_reader(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.snapshot_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    @staticmethod
    def _turn_key(state: WorldState) -> tuple[Any, ...]:
        village = state.village
        if village is not None:
            return ("village", village.minute // 5)
        return ("world", sum(actor.action_clock for actor in state.actors.values()))

    def _record_snapshot(self, state: WorldState) -> None:
        with closing(self._connect_snapshots()) as connection, connection:
            latest = connection.execute(
                "SELECT state, turn, turn_start FROM snapshots ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            encoded = self._snapshot_document(state)
            if latest is not None and (latest[1] is None or latest[2] is None):
                self._backfill_turns(connection)
                latest = connection.execute(
                    "SELECT state, turn, turn_start FROM snapshots ORDER BY sequence DESC LIMIT 1"
                ).fetchone()
            if latest is not None and latest[0] == encoded:
                return
            minute = state.village.minute if state.village else None
            previous = (WorldState.model_validate(json.loads(zlib.decompress(latest[0])))
                        if latest is not None else None)
            starts_turn = previous is None or self._turn_key(previous) != self._turn_key(state)
            turn = 1 if previous is None else int(latest[1]) + int(starts_turn)
            connection.execute(
                "INSERT INTO snapshots(revision, minute, state, turn, turn_start) VALUES (?, ?, ?, ?, ?)",
                (state.revision, minute, encoded, turn, int(starts_turn)))

    def _backfill_turns(self, connection: sqlite3.Connection) -> None:
        previous_key: tuple[Any, ...] | None = None
        turn = 0
        rows = connection.execute(
            "SELECT sequence, state FROM snapshots ORDER BY sequence"
        ).fetchall()
        for sequence, compressed in rows:
            state = WorldState.model_validate(json.loads(zlib.decompress(compressed)))
            key = self._turn_key(state)
            starts_turn = previous_key != key
            turn += int(starts_turn)
            connection.execute(
                "UPDATE snapshots SET turn = ?, turn_start = ? WHERE sequence = ?",
                (turn, int(starts_turn), sequence))
            previous_key = key

    def snapshots(self) -> list[dict[str, Any]]:
        if not self.path.exists() or not self.snapshot_path.exists():
            return []
        with closing(self._connect_snapshot_reader()) as connection:
            rows = connection.execute(
                "SELECT sequence, revision, minute, turn, turn_start FROM snapshots "
                "ORDER BY sequence"
            ).fetchall()
        return [{"sequence": row[0], "revision": row[1], "minute": row[2],
                 "turn": row[3] or 0, "turn_start": bool(row[4])} for row in rows]

    def snapshot(self, sequence: int) -> WorldState:
        if not self.snapshot_path.exists():
            raise ValueError("World playback checkpoint not found.")
        with closing(self._connect_snapshot_reader()) as connection:
            row = connection.execute(
                "SELECT state FROM snapshots WHERE sequence = ?", (sequence,)
            ).fetchone()
        if row is None:
            raise ValueError("World playback checkpoint not found.")
        return WorldState.model_validate(json.loads(zlib.decompress(row[0])))

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
