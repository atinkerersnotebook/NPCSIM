from typing import Any

from jsonschema import Draft202012Validator, validate


OBSERVATION_SCHEMA = {
    "type": "object",
    "required": ["contract_version", "world_id", "session_id", "observation_revision",
                 "self", "location", "narrative", "visible_entities", "actions"],
    "properties": {
        "contract_version": {"const": "1.0"},
        "world_id": {"type": "string"}, "session_id": {"type": "string"},
        "observation_revision": {"type": "string"},
        "self": {"type": "object", "required": ["actor_id", "location_id", "body", "inventory", "needs"],
                 "properties": {"actor_id": {"type": "string"}, "location_id": {"type": "string"},
                                "body": {"type": "object"}, "inventory": {"type": "array"},
                                "needs": {"type": "array"}}},
        "location": {"type": "object", "required": ["id", "description"]},
        "narrative": {"type": "string"},
        "secondary_goals": {"type": "array", "items": {
            "type": "object", "required": ["id", "title", "description", "status", "conditions"],
            "properties": {"id": {"type": "string"}, "title": {"type": "string"},
                           "description": {"type": "string"}, "status": {"enum": ["active", "achieved"]},
                           "conditions": {"type": "array", "items": {"type": "string"}}},
            "additionalProperties": False}},
        "skill_challenges": {"type": "array", "items": {
            "type": "object", "required": ["id", "description", "target", "capability", "completed_by", "reason"],
            "properties": {"id": {"type": "string"}, "description": {"type": "string"},
                           "target": {"type": "string"}, "capability": {"type": "string"},
                           "completed_by": {"type": ["string", "null"]}, "reason": {"type": ["string", "null"]}}}},
        "discovered_goal": {
            "type": "object", "required": ["objective", "type", "source_id"],
            "properties": {"objective": {"type": "string", "minLength": 1},
                           "type": {"type": "string", "minLength": 1},
                           "source_id": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        },
        "visible_entities": {"type": "array", "items": {"type": "object", "required": ["id", "description"]}},
        "actions": {"type": "array", "items": {
            "type": "object", "required": ["id", "description", "arguments_schema", "availability"],
            "properties": {"id": {"type": "string"}, "description": {"type": "string"},
                           "arguments_schema": {"type": "object"},
                           "availability": {"enum": ["available", "unknown", "blocked"]}},
        }},
    },
}


def observation(payload: dict[str, Any], session_id: str | None = None) -> dict[str, Any]:
    validate(payload, OBSERVATION_SCHEMA)
    for action in payload["actions"]:
        Draft202012Validator.check_schema(action["arguments_schema"])
    if session_id is not None and payload["session_id"] != session_id:
        raise ValueError("World response belongs to a different actor session")
    return payload


def action_result(payload: dict[str, Any], request_id: str, session_id: str) -> dict[str, Any]:
    validate(payload, {
        "type": "object",
        "required": ["session_id", "request_id", "outcome", "code", "explanation", "observation"],
        "properties": {"session_id": {"const": session_id}, "request_id": {"const": request_id},
                       "outcome": {"enum": ["APPLIED", "NO_CHANGE", "REJECTED"]},
                       "code": {"type": "string"}, "explanation": {"type": "string"}},
    })
    observation(payload["observation"], session_id)
    return payload


def goal_result(payload: dict[str, Any]) -> dict[str, Any]:
    validate(payload, {"type": "object", "required": ["status", "evidence"],
                       "properties": {"status": {"enum": ["active", "achieved", "blocked", "failed"]},
                                      "evidence": {"type": "object"}}})
    return payload
