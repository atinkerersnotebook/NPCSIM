import copy
import json

import pytest

from npc_sim.assessment import Finding
from npc_sim.dungeon_master import DungeonMasterConfig, DungeonMasterReview, validate_review
from npc_sim.models import ProxyConfig
from npc_sim.prompt_context import RECENT_ACTIONS, RECENT_MESSAGES, REVIEW_EVENTS, review_context
from npc_sim.proxy import Proxy


def observed(actor="smith"):
    return {
        "contract_version": "1.0", "world_id": "world", "session_id": actor,
        "observation_revision": "revision-7", "opening": False, "narrative": "At the forge.",
        "self": {"actor_id": actor, "location_id": "forge", "inventory": ["hammer"],
                 "body": {"grips": ["hand"], "abilities": ["forge"]}, "needs": ["food"]},
        "location": {"id": "forge", "description": "A workshop."},
        "role": "Repair the wagon without inventing supplies.",
        "visible_entities": [
            {"id": "wagon", "name": "Wagon", "kind": "item", "status": "broken",
             "description": "A detailed description. " * 200},
            {"id": "instructions", "description": "A readable note.", "text": "Use two planks."},
        ],
        "actions": [{"id": "forge", "description": "Use observed supplies.",
                     "arguments_schema": {"type": "object", "description": "Schema details. " * 400},
                     "availability": "blocked", "reason": "Missing iron.",
                     "target_requirements": {"wagon": ["forge"]}, "targets": ["wagon"]}],
        "messages": [{"text": f"Message {i}"} for i in range(50)],
        "secondary_goals": [{"id": "well", "status": "active", "conditions": ["bring timber"]}],
        "village": {
            "minute": 725, "policy": "Wagons cannot use footpaths.",
            "stocks": {"iron": 0}, "coins": 4,
            "events": [{"minute": i, "result": f"Event {i}"} for i in range(100)],
            "notices": [{"minute": i, "result": f"Notice {i}"} for i in range(100)],
            "personal": {
                "personality": "Patient.", "routine": ["breakfast", "work"],
                "beliefs": {"shovel": {"location": "stable", "learned_at": 700}},
                "known_routes": ["forge-square"], "available_at": 730,
                "stock_report": {"farm": {"grain": 4}}, "stock_report_at": 710,
                "deadlines": [{"due": 1140, "status": "pending"}],
                "life": {"hungry": True, "sleeping": False, "work": {"finishes_at": 750}},
            },
        },
        "erp": {"pending_orders": ["order-1"]},
    }


def remembered():
    return {
        "agent_profile": {"name": "Mira", "operating_rules": ["Do not teleport."]},
        "recent": [{"action_id": "work", "explanation": str(i)} for i in range(30)],
        "plan": ["Collect iron"], "subgoal": "Finish the wagon",
        "known_entities": {"shovel": {"last_seen_location": "stable", "use_grips": ["hand"]}},
        "visited_locations": {"stable": "A stable."}, "discoveries": ["shovel"],
        "operator_conversation": [{"question": str(i), "answer": str(i)} for i in range(30)],
        "previous_attempt_feedback": {
            "kind": "previous experience",
            "failures": [{"run_id": str(i), "findings": [{"reason": "Missing supplies."}]} for i in range(12)],
        },
    }


@pytest.mark.parametrize("private_question", [False, True])
def test_npc_context_keeps_current_constraints_and_known_facts_but_bounds_short_term_memory(private_question):
    proxy = Proxy(ProxyConfig())
    goal = {"objective": "Repair the wagon", "revealed": True}
    observation, memory = observed(), remembered()
    before = copy.deepcopy((observation, memory))
    messages = proxy.question_context(goal, observation, memory, "What are you doing?") if private_question \
        else proxy.context(goal, observation, memory, "Use only available actions.")
    value = json.loads(messages[1]["content"])
    assert value["goal"] == goal
    assert value["observation"]["self"] == observation["self"]
    assert value["observation"]["actions"] == observation["actions"]
    assert value["observation"]["secondary_goals"] == observation["secondary_goals"]
    assert value["observation"]["village"]["personal"] == observation["village"]["personal"]
    assert len(value["observation"]["village"]["events"]) == RECENT_MESSAGES
    assert value["observation"]["village"]["notices"] == observation["village"]["notices"][-RECENT_MESSAGES:]
    assert value["observation"]["messages"] == observation["messages"][-RECENT_MESSAGES:]
    assert value["memory"]["recent"] == memory["recent"][-RECENT_ACTIONS:]
    assert value["memory"]["operator_conversation"] == memory["operator_conversation"][-RECENT_MESSAGES:]
    assert value["memory"]["previous_attempt_feedback"]["failures"] == memory["previous_attempt_feedback"]["failures"][-3:]
    for key in ("known_entities", "visited_locations", "discoveries", "plan", "subgoal"):
        assert value["memory"][key] == memory[key]
    assert "agent_profile" not in value["memory"]
    assert "Do not teleport." in messages[0]["content"]
    assert "team" not in value
    if private_question:
        assert value["operator_question"] == "What are you doing?"
    else:
        assert value["validation_error"] == "Use only available actions."
    assert (observation, memory) == before


def review_payload(count=REVIEW_EVENTS):
    return {
        "config": DungeonMasterConfig().model_dump(mode="json"),
        "goal": {"objective": "Repair the wagon"},
        "team": {f"actor-{i}": {"role": "Villager", "observation": observed(f"actor-{i}")} for i in range(12)},
        "evidence": [
            {"id": f"run:{i}", "kind": "action_result", "actor_id": f"actor-{i % 12}", "village_minute": 725,
             "payload": {"request_id": str(i), "outcome": "REJECTED", "code": "MISSING_IRON",
                         "explanation": "No iron available. " * 500, "observation": observed(f"actor-{i % 12}")}}
            for i in range(count)
        ],
    }


def test_review_deduplicates_static_context_and_summarizes_evidence_without_losing_authority():
    payload = review_payload()
    original = copy.deepcopy(payload)
    proxy = Proxy(ProxyConfig())
    messages = proxy.dungeon_master_context(payload, "Cite a supplied event.")
    value = json.loads(messages[1]["content"])
    assert len(value["team"]) == 12
    assert value["goal"] == payload["goal"] and value["config"] == payload["config"]
    for actor, member in value["team"].items():
        latest = member["observation"]
        assert latest["self"] == payload["team"][actor]["observation"]["self"]
        assert latest["actions"][0]["availability"] == "blocked"
        assert latest["actions"][0]["reason"] == "Missing iron."
        assert latest["actions"][0]["target_requirements"] == {"wagon": ["forge"]}
        assert "arguments_schema" not in latest["actions"][0]
        assert latest["visible_entities"][0]["status"] == "broken"
        assert latest["visible_entities"][1]["text"] == "Use two planks."
        assert latest["village"]["personal"]["life"]["hungry"]
        assert latest["village"]["personal"]["known_routes"] == ["forge-square"]
        assert "beliefs" not in latest["village"]["personal"]
        assert "events" not in latest["village"] and "notices" not in latest["village"]
        assert latest["erp"] == {"pending_orders": ["order-1"]}
    assert value["world_policies"] == ["Wagons cannot use footpaths."]
    assert len(value["recent_notices"]) == RECENT_MESSAGES
    assert value["validation_error"] == "Cite a supplied event."
    assert "omissions are not evidence of absence or futility" in messages[0]["content"]
    assert [event["id"] for event in value["evidence"]] == [event["id"] for event in payload["evidence"]]
    for event in value["evidence"]:
        result = event["payload"]
        assert result["outcome"] == "REJECTED" and result["code"] == "MISSING_IRON"
        assert result["explanation"].endswith("[truncated]") and result["explanation_truncated"]
        assert "actions" not in result["observation"] and "visible_entities" not in result["observation"]
        assert result["observation"]["village"]["personal"]["available_at"] == 730
        assert result["observation"]["erp"] == {"pending_orders": ["order-1"]}
    assert len(json.dumps(value)) < len(json.dumps(original)) * 0.15
    review = DungeonMasterReview(verdict="continue", findings=[
        Finding(reason="Supplies can still be found.", evidence_ids=["run:39"])])
    validate_review(review, value["evidence"], DungeonMasterConfig())
    assert payload == original


def test_review_window_keeps_original_event_ids_and_verifications_and_does_not_invent_unknown_positions():
    payload = review_payload(REVIEW_EVENTS + 2)
    payload["team"]["unknown"] = {"role": "Unbound", "observation": None}
    payload["evidence"][-2] = {"id": "run:observation", "kind": "observation", "payload": observed()}
    payload["evidence"][-1] = {
        "id": "run:verification", "kind": "verification",
        "payload": {"status": "active", "evidence": {"well_progress": "4/6"}},
    }
    value = review_context(payload)
    assert len(value["evidence"]) == REVIEW_EVENTS
    assert value["evidence"][0]["id"] == "run:2"
    assert value["evidence"][-1] == payload["evidence"][-1]
    assert value["evidence"][-2]["payload"]["self"]["location_id"] == "forge"
    assert value["team"]["unknown"]["observation"] is None
    invalid = DungeonMasterReview(verdict="defeat", findings=[
        Finding(reason="Invalid citation.", evidence_ids=["run:0"])])
    with pytest.raises(ValueError, match="outside"):
        validate_review(invalid, value["evidence"], DungeonMasterConfig())


def test_short_term_result_text_is_bounded_without_trimming_current_action_schemas_or_saved_memory():
    observation, memory = observed(), remembered()
    memory["recent"][-1]["explanation"] = "Long result. " * 1000
    before = copy.deepcopy(memory)
    messages = Proxy(ProxyConfig()).context({"objective": "Continue"}, observation, memory)
    payload = json.loads(messages[-1]["content"])
    result = payload["memory"]["recent"][-1]
    assert len(result["explanation"]) < 650
    assert result["explanation"].endswith("[truncated]")
    assert result["explanation_truncated"]
    assert payload["observation"]["actions"] == observation["actions"]
    assert memory == before
