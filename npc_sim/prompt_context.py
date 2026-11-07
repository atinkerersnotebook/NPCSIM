import copy
from typing import Any


RECENT_ACTIONS = 6
RECENT_MESSAGES = 6
PREVIOUS_FAILURES = 3
REVIEW_EVENTS = 40
RESULT_CHARACTERS = 600


def _trim_explanation(value: dict[str, Any]) -> None:
    explanation = value.get("explanation")
    if isinstance(explanation, str) and len(explanation) > RESULT_CHARACTERS:
        value["explanation"] = explanation[:RESULT_CHARACTERS].rstrip() + " [truncated]"
        value["explanation_truncated"] = True


def observation_context(value: dict[str, Any], *, supervisor: bool = False) -> dict[str, Any]:
    result = copy.deepcopy(value)
    if "messages" in result:
        result["messages"] = result["messages"][-RECENT_MESSAGES:]
    village = result.get("village")
    if village is not None:
        for key in ("events", "notices"):
            if key in village:
                village[key] = village[key][-RECENT_MESSAGES:]
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


def memory_context(value: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(value)
    result.pop("agent_profile", None)
    if "recent" in result:
        result["recent"] = result["recent"][-RECENT_ACTIONS:]
        for item in result["recent"]:
            _trim_explanation(item)
    if "operator_conversation" in result:
        result["operator_conversation"] = result["operator_conversation"][-RECENT_MESSAGES:]
    feedback = result.get("previous_attempt_feedback")
    if feedback is not None and "failures" in feedback:
        feedback["failures"] = feedback["failures"][-PREVIOUS_FAILURES:]
    return result


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
