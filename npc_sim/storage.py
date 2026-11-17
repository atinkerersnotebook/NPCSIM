import json
import logging
import os
import re
import sqlite3
import threading
import tempfile
import time
from pathlib import Path
from typing import Any


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


class StoryStore:
    """Transactional narrative cursor and bounded, indexed pages for one episode."""

    def __init__(self, directory: Path, run_id: str, *, team: bool = False):
        self.path = directory / "story.sqlite3"
        self.run_id = run_id
        self.team = team
        with _story_locks_guard:
            self._lock = _story_locks.setdefault(self.path.resolve(), threading.RLock())
        with self._lock, self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS progress (id INTEGER PRIMARY KEY CHECK(id=1), sequence INTEGER NOT NULL, context TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS paragraphs (source_sequence INTEGER PRIMARY KEY, source_id TEXT NOT NULL, actor_id TEXT NOT NULL, day INTEGER, text TEXT NOT NULL, version INTEGER NOT NULL)")
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
            paragraphs = build_narrative([event], actor, state)
            merged = state.pop("merged", None)
            if merged:
                self._insert(db, merged, actor, day, context.get("pending_meta", {}).get(actor, {}))
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
                context.setdefault("pending_meta", {})[actor] = {
                    key: event[key] for key in ("village_minute", "free_action", "actor_name") if key in event}
            db.execute("UPDATE progress SET sequence=?, context=? WHERE id=1",
                       (event["sequence"], json.dumps(context, ensure_ascii=True)))

    @staticmethod
    def _text(text: str, actor: str, meta: dict[str, Any]) -> str:
        minute = meta.get("village_minute")
        if minute is None:
            return text

        def clock(match: re.Match[str]) -> str:
            value = int(match[2])
            time = f"{value % 1440 // 60:02}:{value % 60:02}"
            return f"{match[1]} {time}" + (f" on day {value // 1440 + 1}" if value >= 1440 else "")

        text = re.sub(r"\b(at|until|by|after|before|since|due) minute (\d{1,15})\b",
                      clock, text, flags=re.IGNORECASE)
        name = meta.get("actor_name") or actor.replace("-", " ").replace("_", " ").title()
        label = f"Day {minute // 1440 + 1} {minute % 1440 // 60:02}:{minute % 60:02} / {name}"
        return f"[{label}{' / FREE LOOK/READ' if meta.get('free_action') else ''}] {text}"

    def _insert(self, db: sqlite3.Connection, paragraph: dict[str, str], actor: str,
                day: int | None, meta: dict[str, Any]) -> None:
        sequence = int(paragraph["id"].rsplit(":", 1)[1])
        db.execute("INSERT OR IGNORE INTO paragraphs VALUES (?, ?, ?, ?, ?, 1)",
                   (sequence, paragraph["id"], actor, day, self._text(paragraph["text"], actor, meta)))

    def page(self, *, day: int | None = None, actor: str | None = None,
             after: int = 0, limit: int = 100) -> dict[str, Any]:
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
            rows = db.execute("SELECT source_sequence, source_id, actor_id, day, text FROM paragraphs WHERE " +
                              " AND ".join(conditions) + " ORDER BY source_sequence LIMIT ?", (*params, limit + 1)).fetchall()
            days = {row[0] for row in db.execute("SELECT DISTINCT day FROM paragraphs WHERE day IS NOT NULL")}
            pending: list[dict[str, Any]] = []
            for key, state in context.get("actors", {}).items():
                paragraph = state.get("pending")
                pending_day = context.get("pending_day", {}).get(key)
                if paragraph and (actor is None or actor == key) and (day is None or day == pending_day):
                    position = int(paragraph["id"].rsplit(":", 1)[1])
                    if position > after:
                        pending.append({"sequence": position, "id": paragraph["id"], "actor_id": key,
                                        "day": pending_day, "text": self._text(paragraph["text"], key,
                                            context.get("pending_meta", {}).get(key, {})), "version": 1})
                        if pending_day is not None:
                            days.add(pending_day)
            entries = [{"sequence": seq, "id": sid, "actor_id": who, "day": when, "text": text, "version": 1}
                       for seq, sid, who, when, text in rows]
            entries = sorted(entries + pending, key=lambda item: item["sequence"])
            return {"run_id": self.run_id, "cursor": sequence, "days": sorted(days),
                    "paragraphs": entries[:limit], "next": entries[limit - 1]["sequence"] if len(entries) > limit else None}
