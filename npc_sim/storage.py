import json
import logging
import os
import re
import sqlite3
import threading
import tempfile
import time
import zlib
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from npc_sim.report_formats import REPORT_FORMATS, format_story_entry


LOGGER = logging.getLogger(__name__)
_story_locks: dict[Path, threading.RLock] = {}
_story_locks_guard = threading.Lock()


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


class EventSequence(Sequence[dict[str, Any]]):
    def __init__(self, store: "EventStore"):
        self._store = store

    def __len__(self) -> int:
        return self._store.count

    def __getitem__(self, index: int | slice) -> dict[str, Any] | list[dict[str, Any]]:
        if isinstance(index, slice):
            return [self[position] for position in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError(index)
        return self._store.get(index + 1)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        for sequence in range(1, len(self) + 1):
            yield self._store.get(sequence)

    def __reversed__(self) -> Iterator[dict[str, Any]]:
        for sequence in range(len(self), 0, -1):
            yield self._store.get(sequence)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, (list, EventSequence)):
            return False
        return list(self) == list(other)

    def __deepcopy__(self, memo: dict[int, Any]) -> list[dict[str, Any]]:
        from copy import deepcopy
        return [deepcopy(event, memo) for event in self]

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        with self._store._lock:
            event = dict(event)
            event.setdefault("sequence", self._store.count + 1)
            self._store.count = max(self._store.count, event["sequence"])
            self._store._cache[event["sequence"]] = event
            return event


class EventStore:
    ARCHIVE_DATABASE = "events.sqlite3"
    RECENT_EVENT_COUNT = 500
    COMPACT_BATCH_SIZE = 500

    def __init__(self, directory: Path, run_id: str, *, validate: bool = True):
        self.directory = directory
        self.run_id = run_id
        self.validate = validate
        self._lock = threading.RLock()
        self._cache: dict[int, dict[str, Any]] = {}
        self.count = 0
        self._initialize_archive()
        with sqlite3.connect(self._archive_path) as connection:
            archived = connection.execute("SELECT MAX(sequence) FROM events").fetchone()[0] or 0
        self.rebuild_index()
        recent = sorted((directory / "events").glob("*.json"))
        if recent:
            try:
                latest = int(recent[-1].stem)
            except ValueError as error:
                raise ValueError(f"Invalid event document: {recent[-1].name}") from error
            self.count = max(archived, latest)
        else:
            self.count = archived
        self._check_contiguous(archived)
        self.events: Sequence[dict[str, Any]] = EventSequence(self)
        self._index()

    @property
    def _archive_path(self) -> Path:
        return self.directory / self.ARCHIVE_DATABASE

    def _initialize_archive(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._archive_path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY, kind TEXT, "
                "actor_id TEXT, cycle INTEGER, payload BLOB NOT NULL)")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
            for name, declaration in (("kind", "TEXT"), ("actor_id", "TEXT"), ("cycle", "INTEGER")):
                if name not in columns:
                    connection.execute(f"ALTER TABLE events ADD COLUMN {name} {declaration}")
            connection.execute("CREATE INDEX IF NOT EXISTS events_kind_sequence ON events(kind, sequence)")

    def _check_contiguous(self, archived_through: int) -> None:
        with sqlite3.connect(self._archive_path) as connection:
            archived_count = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        if archived_count != archived_through:
            raise ValueError("Non-contiguous event archive")
        remaining = {int(path.stem) for path in (self.directory / "events").glob("*.json")}
        expected = set(range(archived_through + 1, self.count + 1))
        if remaining != expected:
            raise ValueError("Non-contiguous event sequence or duplicate archive entry")

    @staticmethod
    def _decode(payload: bytes) -> dict[str, Any]:
        return json.loads(zlib.decompress(payload))

    @staticmethod
    def _encode(event: dict[str, Any]) -> bytes:
        raw = json.dumps(event, ensure_ascii=True, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
        return zlib.compress(raw, level=6)

    def get(self, sequence: int) -> dict[str, Any]:
        if sequence < 1 or sequence > self.count:
            raise IndexError(sequence - 1)
        with self._lock:
            cached = self._cache.get(sequence)
            if cached is not None:
                return cached
            with sqlite3.connect(self._archive_path) as connection:
                row = connection.execute(
                    "SELECT payload FROM events WHERE sequence = ?", (sequence,)).fetchone()
            if row is not None:
                event = self._decode(row[0])
            else:
                path = self.directory / "events" / f"{sequence:06}.json"
                event = read_json(path)
            if self.validate and (event.get("schema_version") != "1.0" or event.get("run_id") != self.run_id
                    or event.get("sequence") != sequence):
                raise ValueError(f"Invalid event document for sequence {sequence}")
            if len(self._cache) >= 64:
                self._cache.pop(next(iter(self._cache)))
            self._cache[sequence] = event
            return event

    def page(self, after: int, limit: int) -> list[dict[str, Any]]:
        stop = min(self.count, max(0, after) + limit)
        return [self.get(sequence) for sequence in range(max(0, after) + 1, stop + 1)]

    def compact(self, batch_size: int | None = None) -> int:
        batch_size = batch_size or self.COMPACT_BATCH_SIZE
        with self._lock:
            target = max(0, self.count - self.RECENT_EVENT_COUNT)
            with sqlite3.connect(self._archive_path) as connection:
                archived = connection.execute("SELECT MAX(sequence) FROM events").fetchone()[0] or 0
                for duplicate in (self.directory / "events").glob("*.json"):
                    if int(duplicate.stem) <= archived:
                        duplicate.unlink()
                stop = min(target, archived + batch_size)
                if stop <= archived:
                    return 0
                entries: list[tuple[int, bytes]] = []
                consumed: list[Path] = []
                for sequence in range(archived + 1, stop + 1):
                    path = self.directory / "events" / f"{sequence:06}.json"
                    event = read_json(path)
                    self._validate(event, sequence)
                    entries.append((sequence, event.get("kind"), event.get("actor_id"), event.get("cycle"),
                                    self._encode(event)))
                    consumed.append(path)
                with connection:
                    connection.executemany(
                        "INSERT INTO events(sequence, kind, actor_id, cycle, payload) VALUES (?, ?, ?, ?, ?)",
                        entries)
                for path in consumed:
                    path.unlink()
                return len(entries)

    def rebuild_index(self, batch_size: int = 1000) -> int:
        updated = 0
        with sqlite3.connect(self._archive_path) as connection:
            while True:
                rows = connection.execute(
                    "SELECT sequence, payload FROM events WHERE kind IS NULL "
                    "ORDER BY sequence LIMIT ?", (batch_size,)).fetchall()
                if not rows:
                    break
                with connection:
                    for sequence, payload in rows:
                        event = self._decode(payload)
                        connection.execute(
                            "UPDATE events SET kind = ?, actor_id = ?, cycle = ? WHERE sequence = ?",
                            (event.get("kind"), event.get("actor_id"), event.get("cycle"), sequence))
                updated += len(rows)
        return updated

    def matching(self, *, kinds: set[str], reverse: bool = False,
                 limit: int | None = None) -> list[dict[str, Any]]:
        order = "DESC" if reverse else "ASC"
        placeholders = ",".join("?" for _ in kinds)
        query = f"SELECT sequence FROM events WHERE kind IN ({placeholders}) ORDER BY sequence {order}"
        parameters: list[Any] = list(kinds)
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(limit)
        archived: list[int]
        with sqlite3.connect(self._archive_path) as connection:
            archived = [row[0] for row in connection.execute(query, parameters)]
        recent = []
        archived_end = min(self.count - self.RECENT_EVENT_COUNT, self.count)
        for sequence in range(max(1, archived_end + 1), self.count + 1):
            try:
                event = self.get(sequence)
            except (FileNotFoundError, IndexError):
                continue
            if event.get("kind") in kinds:
                recent.append(sequence)
        if reverse:
            recent.reverse()
        sequences = (*recent, *archived) if reverse else (*archived, *recent)
        if limit is not None:
            sequences = sequences[:limit]
        return [self.get(sequence) for sequence in sequences]

    def _validate(self, event: dict[str, Any], sequence: int) -> None:
        if (event.get("schema_version") != "1.0" or event.get("run_id") != self.run_id
                or event.get("sequence") != sequence):
            raise ValueError(f"Invalid event document for sequence {sequence}")

    def _index(self) -> None:
        if self.count > 100_000:
            raise ValueError("Event index is too large to materialize")
        write_json(self.directory / "transcript.json", {
            "schema_version": "1.0", "run_id": self.run_id,
            "events": [f"{self.run_id}:{sequence}" for sequence in range(1, self.count + 1)],
            "count": self.count,
        })

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        sequence = self.count + 1
        entry = {**event, "schema_version": "1.0", "run_id": self.run_id,
                 "sequence": sequence, "id": f"{self.run_id}:{sequence}"}
        write_json(self.directory / "events" / f"{sequence:06}.json", entry)
        self.count = sequence
        if sequence <= self.RECENT_EVENT_COUNT or sequence % self.COMPACT_BATCH_SIZE == 0:
            self._index()
        with self._lock:
            self._cache[sequence] = entry
        return entry


class StoryStore:
    """Transactional narrative cursor and bounded, indexed pages for one episode."""

    def __init__(self, directory: Path, run_id: str, *, team: bool = False,
                 actor_names: dict[str, str] | None = None):
        self.path = directory / "story.sqlite3"
        self.run_id = run_id
        self.team = team
        self.actor_names = actor_names or {}
        with _story_locks_guard:
            self._lock = _story_locks.setdefault(self.path.resolve(), threading.RLock())
        with self._lock, self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS progress (id INTEGER PRIMARY KEY CHECK(id=1), sequence INTEGER NOT NULL, context TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS paragraphs (source_sequence INTEGER PRIMARY KEY, source_id TEXT NOT NULL, actor_id TEXT NOT NULL, day INTEGER, text TEXT NOT NULL, version INTEGER NOT NULL)")
            columns = {row[1] for row in db.execute("PRAGMA table_info(paragraphs)")}
            if "report_meta" not in columns:
                db.execute("ALTER TABLE paragraphs ADD COLUMN report_meta TEXT NOT NULL DEFAULT '{}'")
            db.execute("CREATE INDEX IF NOT EXISTS story_day ON paragraphs(day, source_sequence)")
            db.execute("INSERT OR IGNORE INTO progress VALUES (1, 0, '{}')")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def position(self) -> int:
        with self._lock, self._connect() as db:
            return db.execute("SELECT sequence FROM progress WHERE id=1").fetchone()[0]

    def days(self) -> list[int]:
        with self._lock, self._connect() as db:
            sequence, raw = db.execute("SELECT sequence, context FROM progress WHERE id=1").fetchone()
            context = json.loads(raw)
            days = {row[0] for row in db.execute(
                "SELECT DISTINCT day FROM paragraphs WHERE day IS NOT NULL")}
            days.update(day for day in context.get("pending_day", {}).values() if day is not None)
            return sorted(days)


    def append(self, event: dict[str, Any]) -> None:
        from npc_sim.story import build_narrative

        with self._lock, self._connect() as db:
            sequence, raw = db.execute("SELECT sequence, context FROM progress WHERE id=1").fetchone()
            if event["sequence"] <= sequence:
                return
            if event["sequence"] != sequence + 1:
                raise ValueError("Story events must be processed in sequence")
            context = json.loads(raw)
            actor = event["actor_id"]
            state = context.setdefault("actors", {}).setdefault(actor, {"team": self.team})
            if event["kind"] == "observation" and "role" in event["payload"]:
                state["team"] = True
            previous_day = context.get("day")
            day = event.get("village_minute")
            day = day // 1440 + 1 if day is not None else previous_day
            context["day"] = day
            if event["kind"] not in {"started", "goal_discovered", "decision", "observation", "action_request",
                                     "action_result", "error", "finished"}:
                db.execute("UPDATE progress SET sequence=?, context=? WHERE id=1",
                           (event["sequence"], json.dumps(context, ensure_ascii=True)))
                return
            # A request stays in the checkpoint until its result can merge with it.
            pending = state.get("pending")
            old_cycle = state.get("pending_cycle")
            result = event["kind"] == "action_result" and old_cycle == event["cycle"] and pending
            same_day = context.get("pending_day", {}).get(actor) == day
            if pending and (not result or context.get("pending_day", {}).get(actor) != day):
                self._insert(db, pending, actor, context.get("pending_day", {}).get(actor),
                             context.get("pending_meta", {}).get(actor, {}))
                state["pending"] = None
                state["pending_cycle"] = None
            if result and not same_day:
                state["pending"] = None
                state["pending_cycle"] = None
                state["pending_reason"] = ""
            if pending and not result:
                state["pending"] = None
                state["pending_cycle"] = None
            paragraphs = build_narrative([event], actor, state, self.actor_names)
            merged = state.pop("merged", None)
            if merged:
                pending_meta = context.get("pending_meta", {}).get(actor, {})
                report_meta = {key: pending_meta[key] for key in ("action_id", "target") if key in pending_meta}
                report_meta.update(self._report_meta(event))
                self._insert(db, merged, actor, day, report_meta)
            for paragraph in paragraphs:
                if event["kind"] == "observation" and event["payload"].get("opening"):
                    openings = context.setdefault("openings", [])
                    opening = event["payload"]["narrative"]
                    if opening in openings:
                        continue
                    openings.append(opening)
                self._insert(db, paragraph.model_dump(), actor, day, event)
            if state.get("pending"):
                context.setdefault("pending_day", {})[actor] = day
                context.setdefault("pending_meta", {})[actor] = self._report_meta(event)
            db.execute("UPDATE progress SET sequence=?, context=? WHERE id=1",
                       (event["sequence"], json.dumps(context, ensure_ascii=True)))

    def _text(self, text: str, actor: str, meta: dict[str, Any]) -> str:
        minute = meta.get("village_minute")
        slot_name = actor.replace("-", " ").replace("_", " ").title()
        display_name = self.actor_names.get(actor, slot_name)
        if display_name != slot_name:
            text = re.sub(rf"\b{re.escape(slot_name)}\b", display_name, text)
        if minute is None:
            return text

        def clock(match: re.Match[str]) -> str:
            value = int(match[2])
            time = f"{value % 1440 // 60:02}:{value % 60:02}"
            return f"{match[1]} {time}" + (f" on day {value // 1440 + 1}" if value >= 1440 else "")

        text = re.sub(r"\b(at|until|by|after|before|since|due) minute (\d{1,15})\b",
                      clock, text, flags=re.IGNORECASE)
        name = meta.get("actor_name") or display_name
        label = f"Day {minute // 1440 + 1} {minute % 1440 // 60:02}:{minute % 60:02} / {name}"
        return f"[{label}{' / FREE MILESTONE' if meta.get('free_action') else ''}] {text}"

    def _insert(self, db: sqlite3.Connection, paragraph: dict[str, str], actor: str,
                day: int | None, meta: dict[str, Any]) -> None:
        sequence = int(paragraph["id"].rsplit(":", 1)[1])
        db.execute("INSERT OR IGNORE INTO paragraphs "
                   "(source_sequence, source_id, actor_id, day, text, version, report_meta) "
                   "VALUES (?, ?, ?, ?, ?, 1, ?)",
                   (sequence, paragraph["id"], actor, day, self._text(paragraph["text"], actor, meta),
                    json.dumps(self._report_meta(meta), ensure_ascii=True)))

    @staticmethod
    def _report_meta(event: dict[str, Any]) -> dict[str, Any]:
        if "payload" not in event:
            return event
        payload = event.get("payload", {})
        observation = payload if event.get("kind") == "observation" else payload.get("observation", {})
        location = observation.get("self", {}).get("location_id") if isinstance(observation, dict) else None
        arguments = payload.get("arguments", {})
        target = arguments.get("target") if isinstance(arguments, dict) else None
        return {key: value for key, value in (
            ("kind", event.get("kind")),
            ("action_id", payload.get("action_id")),
            ("target", target),
            ("location", location),
            ("outcome", payload.get("outcome")),
            ("status", payload.get("status")),
            ("village_minute", event.get("village_minute")),
            ("free_action", event.get("free_action")),
            ("actor_name", event.get("actor_name")),
        ) if value is not None}

    def page(self, *, day: int | None = None, actor: str | None = None,
             after: int = 0, limit: int = 100,
             report_format: str = "narrative") -> dict[str, Any]:
        if report_format not in REPORT_FORMATS:
            raise ValueError("Unknown report format")
        with self._lock, self._connect() as db:
            sequence, raw = db.execute("SELECT sequence, context FROM progress WHERE id=1").fetchone()
            context = json.loads(raw)
            conditions = ["source_sequence > ?"]
            params: list[Any] = [after]
            if day is not None:
                conditions.append("day = ?")
                params.append(day)
            if actor is not None:
                conditions.append("actor_id = ?")
                params.append(actor)
            rows = db.execute("SELECT source_sequence, source_id, actor_id, day, text, report_meta FROM paragraphs WHERE " +
                              " AND ".join(conditions) + " ORDER BY source_sequence LIMIT ?", (*params, limit + 1)).fetchall()
            days = {row[0] for row in db.execute("SELECT DISTINCT day FROM paragraphs WHERE day IS NOT NULL")}
            pending: list[dict[str, Any]] = []
            for key, state in context.get("actors", {}).items():
                paragraph = state.get("pending")
                pending_day = context.get("pending_day", {}).get(key)
                if paragraph and (actor is None or actor == key) and (day is None or day == pending_day):
                    position = int(paragraph["id"].rsplit(":", 1)[1])
                    if position > after:
                        report_meta = context.get("pending_meta", {}).get(key, {})
                        pending.append({"sequence": position, "id": paragraph["id"], "actor_id": key,
                                        "day": pending_day, "text": self._text(paragraph["text"], key,
                                            report_meta), "version": 1, "report_meta": report_meta})
                        if pending_day is not None:
                            days.add(pending_day)
            entries = [{"sequence": seq, "id": sid, "actor_id": who, "day": when, "text": text,
                        "version": 1, "report_meta": json.loads(meta)}
                       for seq, sid, who, when, text, meta in rows]
            entries = sorted(entries + pending, key=lambda item: item["sequence"])
            next_sequence = entries[limit - 1]["sequence"] if len(entries) > limit else None
            entries = entries[:limit]
            entries = [{key: value for key, value in item.items() if key != "report_meta"} |
                       {"text": self._report_text(item["text"], report_format, item["report_meta"], item["actor_id"])}
                       for item in entries]
            return {"run_id": self.run_id, "cursor": sequence, "days": sorted(days),
                    "paragraphs": entries, "format": report_format,
                    "next": next_sequence}

    @staticmethod
    def _report_text(text: str, report_format: str, meta: dict[str, Any] | None = None,
                     actor: str = "") -> str:
        if report_format == "sitrep":
            details = meta or {}
            kind = details.get("kind")
            outcome = details.get("outcome")
            status = details.get("status")
            action = str(details.get("action_id") or "the action").replace("_", " ")
            progress = {
                "started": "Objective recorded.",
                "goal_discovered": "Objective identified from discovered instructions.",
                "observation": "Situation observed and recorded.",
                "action_request": f"Proposed {action} and recorded the intent.",
                "error": "Attempt interrupted; see Situation for the recorded error.",
                "finished": "Goal verified complete." if status == "succeeded" else f"Attempt ended with status {status or 'unknown'}.",
            }.get(kind, "Story entry recorded.")
            if kind == "action_result":
                progress = "Action applied and its verified result recorded." if outcome == "APPLIED" else f"Action outcome recorded as {outcome or 'unknown'}."
            if outcome == "REJECTED":
                blockers = "The proposed action was rejected; see Situation for the recorded reason."
                next_action = "Reassess the constraints and choose another action."
            elif kind == "error":
                blockers = "The recorded interruption prevented this step from completing."
                next_action = "Recover from the interruption or reassess the approach."
            elif kind == "finished" and status not in {"succeeded", None}:
                blockers = f"The attempt ended with status {status}."
                next_action = "Review the ending before starting another attempt."
            else:
                blockers = "None established by this entry."
                next_action = {
                    "action_request": "Await the verified action result.",
                    "finished": "No further action is required for this completed attempt.",
                }.get(kind, "Continue from this verified event.")
            return ("Situation: " + text + "\nProgress: " + progress +
                    "\nBlockers: " + blockers + "\nNext action: " + next_action)
        if report_format == "bluf":
            return "Current result: " + text
        if report_format == "narrative":
            return text
        return format_story_entry(text, report_format, meta or {}, actor)
