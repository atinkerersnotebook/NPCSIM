"""Display-only projections of recorded story entries."""

import json
import re
from typing import Any


REPORT_FORMATS = frozenset({
    "narrative", "sitrep", "bluf", "brevity", "metar", "salute",
    "telegraphic", "headline", "sbar", "logfmt", "adr", "rfc2119",
})

_EVENT_CODES = {
    "started": "ST", "goal_discovered": "GD", "observation": "OB",
    "action_request": "RQ", "action_result": "RS", "error": "ER", "finished": "FN",
}


def _quote(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _shorten(text: str, *, headline: bool = False) -> str:
    # Keep quoted dialogue and the story's timestamp/actor prefix untouched.
    parts = re.split(r'(\[[^\]]*\]|"[^"]*"|“[^”]*”)', text)
    for index in range(0, len(parts), 2):
        parts[index] = re.sub(r"\b(?:the|a|an)\s+", "", parts[index], flags=re.IGNORECASE)
        if headline:
            parts[index] = re.sub(r"\.\s+(?=[A-Z])", "; ", parts[index])
    return "".join(parts)


def format_story_entry(text: str, report_format: str, meta: dict[str, Any],
                       actor: str) -> str:
    kind = meta.get("kind")
    code = _EVENT_CODES.get(kind, "UN")
    minute = meta.get("village_minute")
    time = f"D{minute // 1440 + 1}-{minute % 1440 // 60:02}{minute % 60:02}" if isinstance(minute, int) else "-"
    location = str(meta.get("location") or "-")
    outcome = meta.get("outcome")
    status = meta.get("status")

    if report_format == "brevity":
        return f"{code} {text}"
    if report_format == "metar":
        return f"{code} {time} {_quote(actor)} {_quote(location)} {_quote(text)}"
    if report_format == "salute":
        return (f"Size: unknown\nActivity: {text}\nLocation: {location}\n"
                f"Unit: unknown\nTime: {time}\nEquipment: unknown")
    if report_format == "telegraphic":
        return _shorten(text)
    if report_format == "headline":
        return _shorten(text, headline=True)
    if report_format == "sbar":
        if kind == "action_result":
            assessment = f"Recorded action outcome: {outcome}." if outcome else "Action outcome not recorded."
        elif kind == "finished":
            assessment = f"Recorded episode status: {status}." if status else "Episode status not recorded."
        else:
            assessment = "No assessment recorded."
        return (f"Situation: {text}\nBackground: Not recorded in this entry.\n"
                f"Assessment: {assessment}\nRecommendation: Not recorded in this entry.")
    if report_format == "adr":
        result = (f"Action outcome: {outcome}." if outcome else
                  f"Episode status: {status}." if status else "Not recorded in this entry.")
        return (f"Context: {text}\nDecision: Not independently recorded in this entry.\n"
                f"Consequences: {result}")
    if report_format == "rfc2119":
        return f"Recorded: {text}"
    if report_format == "logfmt":
        fields = {"npc": actor, "event": kind, "time": time if minute is not None else None,
                  "location": meta.get("location"), "act": meta.get("action_id"),
                  "target": meta.get("target"), "outcome": outcome, "status": status, "text": text}
        return " ".join(f"{key}={_quote(value)}" for key, value in fields.items() if value is not None)
    raise ValueError("Unknown report format")
