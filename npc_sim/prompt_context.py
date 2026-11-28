import copy
import json
from typing import Any


RECENT_ACTIONS = 6
RECENT_MESSAGES = 6
PREVIOUS_FAILURES = 3
REVIEW_EVENTS = 40
RESULT_CHARACTERS = 600
EVENT_RESULT_CHARACTERS = 800
ACTION_DESCRIPTION_CHARACTERS = 240


def _trim_text(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit].rstrip() + " [truncated]"
    return value


def _compact_schema(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _compact_schema(item) for key, item in value.items()
                if key not in {"description", "title", "examples", "default"}}
    if isinstance(value, list):
        return [_compact_schema(item) for item in value]
    return value


def _compact_action(action: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(action)
    if "description" in result:
        result["description"] = _trim_text(result["description"], ACTION_DESCRIPTION_CHARACTERS)
    if "arguments_schema" in result:
        result["arguments_schema"] = _compact_schema(result["arguments_schema"])
    return result


def _compact_event(event: Any) -> Any:
    if not isinstance(event, dict):
        return event
    result = copy.deepcopy(event)
    for key in ("result", "text", "description"):
        if isinstance(result.get(key), str):
            result[key] = _trim_text(result[key], EVENT_RESULT_CHARACTERS)
    result.pop("details", None)
    result.pop("witnesses", None)
    return result


def _trim_explanation(value: dict[str, Any]) -> None:
    explanation = value.get("explanation")
    if isinstance(explanation, str) and len(explanation) > RESULT_CHARACTERS:
        value["explanation"] = explanation[:RESULT_CHARACTERS].rstrip() + " [truncated]"
        value["explanation_truncated"] = True


def observation_context(value: dict[str, Any], *, supervisor: bool = False) -> dict[str, Any]:
    result = copy.deepcopy(value)
    if "actions" in result:
        result["actions"] = [_compact_action(action) for action in result["actions"]]
    if "messages" in result:
        result["messages"] = result["messages"][-RECENT_MESSAGES:]
    village = result.get("village")
    if village is not None:
        communication = village.get("communication")
        if isinstance(communication, dict):
            communication["notices"] = communication.get("notices", [])[:6]
            for notice in communication["notices"]:
                notice.pop("source_chain", None)
            communication["share_candidates"] = communication.get("share_candidates", [])[:3]
        for key in ("events", "notices"):
            if key in village:
                village[key] = [_compact_event(item) for item in village[key][-RECENT_MESSAGES:]]
        for key in ("social", "legacy", "orders", "suggestions"):
            section = village.get(key)
            if isinstance(section, (dict, list)):
                encoded = json.dumps(section, ensure_ascii=True, separators=(",", ":"))
                if len(encoded) > 20_000:
                    village.pop(key, None)
        fallback = village.get("fallback")
        if isinstance(fallback, dict):
            fallback.pop("recent_context_revisions", None)
        if supervisor:
            for key in ("events", "notices", "policy"):
                village.pop(key, None)
            personal = village.get("personal")
            if personal is not None:
                for key in ("personality", "relationships", "routine", "beliefs", "routes"):
                    personal.pop(key, None)
    if supervisor:
        for key in ("contract_version", "world_id", "session_id", "opening", "narrative"):
            result.pop(key, None)
        if "actions" in result:
            result["actions"] = [
                {key: item for key, item in action.items()
                 if key not in {"arguments_schema", "description", "targets"} and item is not None}
                for action in result["actions"]
            ]
        if "visible_entities" in result:
            result["visible_entities"] = [
                {key: item for key, item in entity.items() if key != "description"}
                for entity in result["visible_entities"]
            ]
    return result


def memory_context(value: dict[str, Any], tier: str = "long") -> dict[str, Any]:
    result = copy.deepcopy(value)
    result.pop("agent_profile", None)
    result.pop("scenario_details", None)
    action_context_mode = result.pop("action_context_mode", "structured")
    if "recent" in result:
        result["recent"] = result["recent"][-RECENT_ACTIONS:]
        for item in result["recent"]:
            if action_context_mode == "narrative":
                contextual = item.pop("contextual_description", None)
                if isinstance(contextual, str) and contextual.strip():
                    item["explanation"] = contextual
            elif action_context_mode == "structured":
                item.pop("contextual_description", None)
            _trim_explanation(item)
    if "operator_conversation" in result:
        result["operator_conversation"] = result["operator_conversation"][-RECENT_MESSAGES:]
    feedback = result.get("previous_attempt_feedback")
    if feedback is not None and "failures" in feedback:
        feedback["failures"] = feedback["failures"][-PREVIOUS_FAILURES:]
    for key in ("known_entities", "visited_locations"):
        section = result.get(key)
        if isinstance(section, dict) and len(section) > 40:
            result[key] = dict(list(section.items())[-40:])
    known = result.get("known_entities")
    if isinstance(known, dict):
        retained = {"id", "name", "kind", "location", "last_seen_location", "status", "broken",
                    "open", "locked", "carry_grips", "use_grips", "takable", "readable", "text"}
        result["known_entities"] = {
            key: {field: value for field, value in item.items() if field in retained}
            if isinstance(item, dict) else item for key, item in known.items()}
    if "event_summaries" in result:
        result["event_summaries"] = [{
            **item, "source_events": (item.get("source_events") or [])[-8:]
        } for item in result["event_summaries"][-12:]]
    if "enduring_facts" in result:
        facts = result["enduring_facts"]
        result["enduring_facts"] = facts[-24:] if isinstance(facts, list) else facts
    result.pop("last_observation", None)
    if tier == "short":
        short_keys = {"schema_version", "recent", "discoveries", "plan", "subgoal", "active_typed_step",
                      "replan_warning", "previous_attempt_feedback"}
        result = {key: item for key, item in result.items() if key in short_keys}
        if "recent" in result:
            result["recent"] = result["recent"][-3:]
        feedback = result.get("previous_attempt_feedback")
        if isinstance(feedback, dict) and isinstance(feedback.get("failures"), list):
            feedback["failures"] = feedback["failures"][-1:]
    elif tier == "medium":
        long_only = {"enduring_facts", "operator_conversation"}
        result = {key: item for key, item in result.items() if key not in long_only}
        result["memory_scope"] = "medium"
    elif tier != "long":
        raise ValueError(f"Unknown memory tier: {tier}")
    if tier == "short":
        result["memory_scope"] = "short"
    elif tier == "long":
        result["memory_scope"] = "long"
    return result


def public_proxy_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    def redact(value: Any) -> Any:
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, dict):
            result = {key: redact(item) for key, item in value.items()}
            scenario = result.get("scenario_details")
            if isinstance(scenario, dict) and "private" in scenario:
                scenario.pop("private")
                scenario["npc_private"] = "Omitted from public diagnostics."
            return result
        return value

    result = copy.deepcopy(messages)
    for message in result:
        if message.get("role") != "user":
            continue
        try:
            payload = json.loads(message["content"])
        except (KeyError, TypeError, json.JSONDecodeError):
            continue
        redacted = redact(payload)
        if redacted != payload:
            message["content"] = json.dumps(redacted, ensure_ascii=True, separators=(",", ":"))
    return result


def observation_delta(current: dict[str, Any], previous: Any) -> dict[str, Any]:
    result = observation_context(current)
    if not isinstance(previous, dict):
        return {"current": result, "changed": {"all": True}, "unchanged": []}
    old = observation_context(previous)
    stable = []
    changed = {}
    for key in sorted(set(result) | set(old)):
        if result.get(key) == old.get(key):
            stable.append(key)
        else:
            changed[key] = result.get(key)
    changed["observation_revision"] = result.get("observation_revision")
    changed["self"] = result.get("self")
    changed["location"] = result.get("location")
    changed["actions"] = result.get("actions")
    return {"current_essentials": {key: result.get(key) for key in (
        "observation_revision", "self", "location", "narrative", "actions",
        "visible_entities", "visible_actors", "messages") if key in result},
        "changed": changed, "unchanged": stable,
        "semantics": "Unchanged means omitted from changed, not absent or false."}


def _progress_observation(value: dict[str, Any]) -> dict[str, Any]:
    result = {key: copy.deepcopy(item) for key, item in value.items() if key not in {
        "contract_version", "world_id", "session_id", "self", "location", "opening",
        "narrative", "actions", "visible_entities", "visible_actors", "messages", "role", "village",
    }}
    if "self" in value:
        result["self"] = {key: copy.deepcopy(item) for key, item in value["self"].items()
                          if key in {"actor_id", "location_id", "inventory", "needs"}}
    village = value.get("village")
    if village is not None:
        result["village"] = {key: copy.deepcopy(village[key]) for key in (
            "minute", "day", "time", "stocks", "coins", "resource_inventory", "tasks", "wagon"
        ) if key in village}
        personal = village.get("personal")
        if personal is not None:
            result["village"]["personal"] = {
                key: copy.deepcopy(personal[key]) for key in (
                    "next_work", "busy_until", "available_at", "active_delivery",
                    "shipments", "timber_route", "deadlines", "life"
                ) if key in personal
            }
    return result


def review_context(value: dict[str, Any]) -> dict[str, Any]:
    result = {key: copy.deepcopy(item) for key, item in value.items() if key not in {"team", "evidence"}}
    if "team" in value:
        result["team"] = {
            actor: {**{key: copy.deepcopy(item) for key, item in member.items() if key != "observation"},
                    "observation": observation_context(member["observation"], supervisor=True)
                    if member["observation"] is not None else None}
            for actor, member in value["team"].items()
        }
    evidence = []
    for event in value["evidence"][-REVIEW_EVENTS:]:
        projected = {key: copy.deepcopy(item) for key, item in event.items()
                     if key not in {"payload", "run_id", "session_id"}}
        if "payload" not in event:
            evidence.append(projected)
            continue
        payload = event["payload"]
        if event["kind"] == "observation":
            projected["payload"] = _progress_observation(payload)
        else:
            projected["payload"] = {
                key: copy.deepcopy(item) for key, item in payload.items()
                if key not in {"observation", "session_id", "request_id"}
            }
            if "observation" in payload:
                projected["payload"]["observation"] = _progress_observation(payload["observation"])
            _trim_explanation(projected["payload"])
        evidence.append(projected)
    result["evidence"] = evidence
    result["citation_ids"] = [event["id"] for event in evidence if isinstance(event.get("id"), str)]
    policies: list[str] = []
    notices: list[dict[str, Any]] = []
    for member in value.get("team", {}).values():
        village = (member["observation"] or {}).get("village") or {}
        policy = village.get("policy")
        if policy and policy not in policies:
            policies.append(policy)
        for notice in village.get("notices", [])[-RECENT_MESSAGES:]:
            if notice not in notices:
                notices.append(notice)
    if policies:
        result["world_policies"] = policies
    if notices:
        result["recent_notices"] = copy.deepcopy(
            sorted(notices, key=lambda notice: notice["minute"])[-RECENT_MESSAGES:])
    result["context_window"] = {
        "evidence_limit": REVIEW_EVENTS,
        "evidence_count": len(evidence),
        "history": "Recent progress summaries and latest observed team state, not the complete history.",
    }
    return result
