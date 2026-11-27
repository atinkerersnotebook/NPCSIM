import copy
import json
import re
from typing import Any, Literal


MessageFormat = Literal["normal", "compact", "shorthand", "fact-shorthand"]

KEY_ALIASES = {
    "action_id": "aid",
    "arguments": "arg",
    "arguments_schema": "schema",
    "availability": "avail",
    "available_at": "avail_at",
    "confidence": "conf",
    "creditor": "cr",
    "currency": "cur",
    "debtor": "dr",
    "description": "desc",
    "expected_outcome": "expect",
    "justification": "why",
    "known_entities": "known",
    "location_id": "loc",
    "observation": "obs",
    "operator_question": "question",
    "previous_attempt_feedback": "prior",
    "target_requirements": "targets",
    "validation_error": "error",
    "visible_entities": "visible",
    "visited_locations": "visited",
}

FACT_ORDER = {
    "debt": ("debtor", "creditor", "amount", "currency", "source", "day", "confidence", "status"),
    "owns": ("owner", "item", "quantity", "source", "day", "confidence", "status"),
    "located": ("subject", "location", "source", "day", "confidence", "status"),
    "knows": ("actor", "fact", "source", "day", "confidence", "status"),
    "promised": ("actor", "recipient", "promise", "due", "source", "day", "confidence", "status"),
    "feels_about": ("actor", "target", "feeling", "intensity", "source", "day", "confidence"),
}

_TOKEN_PATTERN = re.compile(r"[a-z0-9_-]{3,}", re.IGNORECASE)


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _keys(item)}
    return set()


def _active_aliases(value: Any) -> dict[str, str]:
    original_keys = _keys(value)
    return {canonical: alias for canonical, alias in KEY_ALIASES.items()
            if alias not in original_keys}


def _encode_keys(value: Any, aliases: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {aliases.get(key, key): _encode_keys(item, aliases) for key, item in value.items()}
    if isinstance(value, list):
        return [_encode_keys(item, aliases) for item in value]
    return value


def _encode_facts(value: Any) -> tuple[Any, int]:
    if isinstance(value, list):
        encoded: list[Any] = []
        count = 0
        for item in value:
            projected, used = _encode_facts(item)
            encoded.append(projected)
            count += used
        return encoded, count
    if isinstance(value, dict):
        kind = str(value.get("type", "")).lower().replace("-", "_")
        order = FACT_ORDER.get(kind)
        if order is not None and all(field in value for field in order[:3]):
            parts = []
            for field in order:
                if field in value:
                    encoded = json.dumps(value[field], ensure_ascii=True, separators=(",", ":"))
                    if isinstance(value[field], str) and not any(char in value[field] for char in ",()"):
                        encoded = value[field]
                    parts.append(f"{field}={encoded}")
            extra = {key: item for key, item in value.items() if key != "type" and key not in order}
            if extra:
                parts.append(f"extra={json.dumps(extra, ensure_ascii=True, separators=(',', ':'))}")
            return {"fact": f"{kind.upper().replace('_', ' ')}({','.join(parts)})"}, 1
        encoded = {}
        count = 0
        for key, item in value.items():
            projected, used = _encode_facts(item)
            encoded[key] = projected
            count += used
        return encoded, count
    return value, 0


def _encode_tables(value: Any) -> tuple[Any, int]:
    if isinstance(value, list):
        projected = [_encode_tables(item)[0] for item in value]
        count = sum(_encode_tables(item)[1] for item in value)
        if len(value) >= 3 and all(isinstance(item, dict) for item in value):
            key_sets = [set(item) for item in value]
            if all(keys == key_sets[0] for keys in key_sets):
                columns = sorted(key_sets[0])
                if columns and all("$table" not in item for item in value):
                    rows = [[projected[index][column] for column in columns]
                            for index in range(len(projected))]
                    return {"$table": {"columns": columns, "rows": rows}}, count + 1
        return projected, count
    if isinstance(value, dict):
        encoded = {}
        count = 0
        for key, item in value.items():
            projected, used = _encode_tables(item)
            encoded[key] = projected
            count += used
        return encoded, count
    return value, 0


def _decode_tables(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"$table"}:
        table = value["$table"]
        if not isinstance(table, dict) or not isinstance(table.get("columns"), list) \
                or not isinstance(table.get("rows"), list):
            raise ValueError("Invalid compressed table")
        columns = table["columns"]
        rows = []
        for row in table["rows"]:
            if not isinstance(row, list) or len(row) != len(columns):
                raise ValueError("Invalid compressed table row")
            rows.append({column: _decode_tables(item) for column, item in zip(columns, row)})
        return rows
    if isinstance(value, dict):
        return {key: _decode_tables(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_tables(item) for item in value]
    return value


def _long_strings(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [item for child in value.values() for item in _long_strings(child)]
    if isinstance(value, list):
        return [item for child in value for item in _long_strings(child)]
    return [value] if isinstance(value, str) and len(value) >= 24 else []


def _deduplicate(value: Any, values: dict[str, int], references: list[Any],
                 repeated: set[str]) -> Any:
    if isinstance(value, list):
        return [_deduplicate(item, values, references, repeated) for item in value]
    if isinstance(value, dict):
        return {key: _deduplicate(item, values, references, repeated) for key, item in value.items()}
    if not isinstance(value, str) or value not in repeated:
        return value
    if value not in values:
        values[value] = len(references)
        references.append(value)
        return {"$ref": values[value]}
    return {"$ref": values[value]}


def _event_signature(event: Any) -> tuple[str, str] | None:
    if not isinstance(event, dict):
        return None
    comparable = {key: value for key, value in event.items()
                  if key not in {"id", "minute", "timestamp", "sequence", "village_minute"}}
    return (str(event.get("kind") or event.get("code") or ""),
            json.dumps(comparable, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


def aggregate_repeated_events(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    result = copy.deepcopy(payload)
    aggregated = 0
    containers: list[dict[str, Any]] = [result]
    village = result.get("observation", {}).get("village")
    if isinstance(village, dict):
        containers.append(village)
    for container in containers:
        for key in ("events", "notices"):
            events = container.get(key)
            if not isinstance(events, list) or len(events) < 3:
                continue
            groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for event in events:
                signature = _event_signature(event)
                if signature is not None and isinstance(event, dict):
                    groups.setdefault(signature, []).append(event)
            replaceable = {signature: items for signature, items in groups.items() if len(items) > 1}
            if not replaceable:
                continue
            output: list[Any] = []
            emitted: set[tuple[str, str]] = set()
            for event in events:
                signature = _event_signature(event)
                items = replaceable.get(signature or ("", ""))
                if items is None:
                    output.append(event)
                    continue
                assert signature is not None
                if signature in emitted:
                    continue
                emitted.add(signature)
                times = [item.get("minute", item.get("village_minute")) for item in items]
                source_ids = [str(item.get("id")) for item in items if item.get("id") is not None]
                output.append({
                    "repeated": dict(items[0]),
                    "count": len(items),
                    "first": min((item for item in times if item is not None), default=None),
                    "last": max((item for item in times if item is not None), default=None),
                    "source_ids": source_ids,
                })
                aggregated += len(items) - 1
            container[key] = output
    return result, aggregated


def select_relevant_memory(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    result = copy.deepcopy(payload)
    memory = result.get("memory")
    if not isinstance(memory, dict):
        return result, 0
    terms: set[str] = set()
    goal = result.get("goal")
    observation = result.get("observation")
    for source in (goal, (observation or {}).get("self"), (observation or {}).get("location")):
        if isinstance(source, dict):
            terms.update(_TOKEN_PATTERN.findall(json.dumps(source, ensure_ascii=True).lower()))
    actions = (observation or {}).get("actions", []) if isinstance(observation, dict) else []
    for action in actions:
        if isinstance(action, dict):
            terms.update(_TOKEN_PATTERN.findall(json.dumps(action, ensure_ascii=True).lower()))
    omitted = 0
    for key in ("facts", "long_term_memory", "memories"):
        entries = memory.get(key)
        if not isinstance(entries, list) or len(entries) <= 24:
            continue
        recent = entries[-6:]
        candidates = entries[:-6]
        ranked = []
        for index, item in enumerate(candidates):
            words = set(_TOKEN_PATTERN.findall(json.dumps(item, ensure_ascii=True).lower()))
            ranked.append((len(words & terms), index, item))
        retained = [item for _, _, item in sorted(ranked, reverse=True)[:18]] + recent
        omitted += len(entries) - len(retained)
        memory[key] = retained
        memory[f"{key}_selection"] = {
            "strategy": "goal-location-action-overlap+recent",
            "retained": len(retained),
            "omitted": len(entries) - len(retained),
        }
    return result, omitted


def encode_prompt_payload(payload: dict[str, Any], mode: MessageFormat, *, deduplicate: bool = False,
                          relevant_memory: bool = False,
                          aggregate_events: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    if mode == "normal" and not (deduplicate or relevant_memory or aggregate_events):
        return payload, {
            "requested_mode": "normal", "effective_mode": "normal", "fallback_reason": None,
            "normal_user_bytes": len(json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()),
            "sent_user_bytes": len(json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()),
            "facts_encoded": 0, "deduplicated_values": 0, "memory_entries_omitted": 0,
            "events_aggregated": 0, "tables_encoded": 0,
        }
    source = copy.deepcopy(payload)
    result = source
    omitted = 0
    aggregated = 0
    if relevant_memory:
        result, omitted = select_relevant_memory(result)
    if aggregate_events:
        result, aggregated = aggregate_repeated_events(result)
    facts_encoded = 0
    expected_facts: list[str] = []
    if mode == "fact-shorthand":
        result, facts_encoded = _encode_facts(result)
        expected_facts = _fact_values(result)
    tables_encoded = 0
    if mode in {"shorthand", "fact-shorthand"}:
        result, tables_encoded = _encode_tables(result)
    aliases = _active_aliases(result)
    if mode != "normal":
        result = _encode_keys(result, aliases)
    dictionary: list[Any] = []
    if deduplicate:
        indexes: dict[str, int] = {}
        from collections import Counter
        repeated = {value for value, count in Counter(_long_strings(result)).items() if count > 1}
        result = _deduplicate(result, indexes, dictionary, repeated)
    schema = {alias: canonical for canonical, alias in aliases.items()
              if _contains_key(result, alias)} if mode != "normal" else {}
    encoded = {
        "compression": {"format": mode, "version": 1, "key_aliases": schema,
                        "references": dictionary if deduplicate else [],
                        "fact_types": sorted(FACT_ORDER) if mode == "fact-shorthand" else [],
                        "fact_roles": {kind: list(fields) for kind, fields in FACT_ORDER.items()}
                        if mode == "fact-shorthand" else {}},
        "data": result,
    }
    normal_text = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    sent_text = json.dumps(encoded, ensure_ascii=True, separators=(",", ":"))
    diagnostics = {
        "requested_mode": mode, "effective_mode": mode, "fallback_reason": None,
        "normal_user_bytes": len(normal_text.encode()), "sent_user_bytes": len(sent_text.encode()),
        "facts_encoded": facts_encoded, "deduplicated_values": len(dictionary),
        "memory_entries_omitted": omitted, "events_aggregated": aggregated,
        "tables_encoded": tables_encoded,
    }
    if mode != "normal":
        decoded = _decode_keys(result, schema)
        decoded = _resolve_references(decoded, dictionary)
        decoded = _decode_tables(decoded)
        expected = copy.deepcopy(payload)
        if relevant_memory:
            expected, _ = select_relevant_memory(expected)
        if aggregate_events:
            expected, _ = aggregate_repeated_events(expected)
        if mode == "fact-shorthand":
            fact_values = _fact_values(decoded)
            if fact_values != expected_facts or (facts_encoded == 0 and expected_facts):
                diagnostics["fallback_reason"] = "fact shorthand validation required a canonical fallback"
                return payload, {
                    **diagnostics, "effective_mode": "normal",
                    "sent_user_bytes": diagnostics["normal_user_bytes"],
                }
        elif decoded != expected:
            diagnostics["fallback_reason"] = "key/reference round-trip validation required a canonical fallback"
            return payload, {
                **diagnostics, "effective_mode": "normal",
                "sent_user_bytes": diagnostics["normal_user_bytes"],
            }
    return encoded, diagnostics


def _decode_keys(value: Any, aliases: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {aliases.get(key, key): _decode_keys(item, aliases) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_keys(item, aliases) for item in value]
    return value


def _resolve_references(value: Any, references: list[Any]) -> Any:
    if isinstance(value, dict) and set(value) == {"$ref"} and isinstance(value["$ref"], int):
        try:
            return copy.deepcopy(references[int(value["$ref"])])
        except (IndexError, TypeError, ValueError) as error:
            raise ValueError("Invalid request-local compression reference") from error
    if isinstance(value, dict):
        return {key: _resolve_references(item, references) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_references(item, references) for item in value]
    return value


def _fact_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        own = [str(value["fact"])] if set(value) == {"fact"} and isinstance(value.get("fact"), str) else []
        return own + [fact for item in value.values() for fact in _fact_values(item)]
    if isinstance(value, list):
        return [fact for item in value for fact in _fact_values(item)]
    return []


def _contains_key(value: Any, target: str) -> bool:
    if isinstance(value, dict):
        return target in value or any(_contains_key(item, target) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, target) for item in value)
    return False


def compression_instructions() -> str:
    return (
        " The user payload may contain a compression object. Decode compression.key_aliases "
        "before interpreting data, and resolve each {$ref:N} from compression.references[N]. "
        "Expand {$table:{columns:C,rows:R}} as records mapping each C value to the corresponding R value. "
        "For compressed facts, apply the ordered named roles in compression.fact_roles exactly. "
        "A fact value is self-describing: its named roles are literal. In DEBT, debtor owes creditor. "
        "Omitted optional values mean unknown, not false. Repeated/source_ids mean exact canonical "
        "event references and must not be treated as one fabricated event. Apply identical safety, "
        "observation and response-schema rules after decoding."
    )
