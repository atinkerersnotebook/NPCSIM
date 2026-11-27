import copy
import json

import pytest

from npc_sim.assessment import Finding
from npc_sim.dungeon_master import DungeonMasterConfig, DungeonMasterReview, validate_review
from npc_sim.models import ProxyConfig
from npc_sim.prompt_context import RECENT_ACTIONS, RECENT_MESSAGES, REVIEW_EVENTS, memory_context, observation_delta, public_proxy_messages, review_context
from npc_sim.proxy import Proxy

from npc_sim.prompt_codec import encode_prompt_payload


def test_observation_delta_is_self_contained_and_non_mutating():
    previous = {"observation_revision": "obs-1", "self": {"location_id": "forge"},
                "location": {"id": "forge"}, "actions": [{"id": "look"}],
                "visible_entities": []}
    current = {**copy.deepcopy(previous), "observation_revision": "obs-2",
               "visible_entities": [{"id": "hammer", "name": "Hammer"}]}

    projected = observation_delta(current, previous)

    assert projected["current_essentials"]["self"] == current["self"]
    assert projected["current_essentials"]["actions"] == current["actions"]
    assert projected["changed"]["visible_entities"] == current["visible_entities"]
    assert projected["semantics"].startswith("Unchanged")
    assert previous["observation_revision"] == "obs-1"


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
    assert [item["id"] for item in value["observation"]["actions"]] == [item["id"] for item in observation["actions"]]
    assert value["observation"]["actions"][0]["arguments_schema"] == {"type": "object"}
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


def test_npc_context_caps_oversized_read_history_without_removing_actions_or_current_facts():
    observation = observed()
    observation["village"]["events"] = [{
        "minute": index, "result": "R" * 5000, "details": {"ignored": "x" * 5000}
    } for index in range(20)]
    observation["village"]["legacy"] = {"chronicle": ["entry " * 20_000]}
    proxy = Proxy(ProxyConfig())
    messages = proxy.context({"objective": "Work", "revealed": True}, observation, remembered())
    value = json.loads(messages[1]["content"])
    assert [item["id"] for item in value["observation"]["actions"]] == [item["id"] for item in observation["actions"]]
    assert value["observation"]["village"]["minute"] == observation["village"]["minute"]
    assert "legacy" not in value["observation"]["village"]
    assert len(value["observation"]["village"]["events"]) == RECENT_MESSAGES
    assert all(len(item["result"]) < 1000 and "details" not in item
               for item in value["observation"]["village"]["events"])


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
    assert [item["id"] for item in payload["observation"]["actions"]] == [item["id"] for item in observation["actions"]]
    assert memory == before


def test_normal_compression_mode_preserves_existing_proxy_messages_exactly():
    proxy = Proxy(ProxyConfig(message_format="normal"))
    messages = proxy.context({"objective": "Continue"}, observed(), remembered())
    payload = json.loads(messages[-1]["content"])
    assert payload["goal"] == {"objective": "Continue"}
    assert payload["observation"]["self"]["actor_id"] == "smith"
    assert "compression" not in payload
    assert proxy.context_diagnostics["effective_mode"] == "normal"
    assert proxy.context_diagnostics["normal_user_bytes"] == proxy.context_diagnostics["sent_user_bytes"]


def test_scenario_private_details_reach_npc_only_and_are_redacted_from_public_messages():
    proxy = Proxy(ProxyConfig())
    memory = {
        "recent": [],
        "scenario_details": {
            "public": "Travel to the observatory.",
            "private": "Your missing sister is imprisoned beneath it.",
        },
    }

    messages = proxy._messages("test", {"scenario_details": memory["scenario_details"]})
    model_payload = json.loads(messages[1]["content"])
    public_messages = public_proxy_messages(messages)
    public_payload = json.loads(public_messages[1]["content"])

    assert model_payload["scenario_details"] == memory["scenario_details"]
    assert public_payload["scenario_details"] == {
        "public": "Travel to the observatory.",
        "npc_private": "Omitted from public diagnostics.",
    }
    assert "imprisoned beneath" not in json.dumps(public_messages)


def test_scenario_details_are_kept_out_of_public_memory_and_in_npc_goal_context():
    proxy = Proxy(ProxyConfig())
    details = {"public": "Travel to the observatory.", "private": "The relic recognizes your voice."}
    messages = proxy.context({"objective": "Explore"}, observed(), {
        "recent": [], "scenario_details": details,
    })
    payload = json.loads(messages[1]["content"])

    assert payload["scenario_details"] == details
    assert "scenario_details" not in payload["memory"]


def test_private_scenario_details_are_available_during_private_npc_questions():
    proxy = Proxy(ProxyConfig())
    memory = {"scenario_details": {"public": "Find the archive.", "private": "The curator is your father."}}

    messages = proxy.question_context({"objective": "Explore"}, observed(), memory, "What matters here?")
    payload = json.loads(messages[1]["content"])

    assert payload["scenario_details"]["private"] == "The curator is your father."


def test_old_proxy_configuration_defaults_to_normal_compression():
    config = ProxyConfig.model_validate({})
    assert config.message_format == "normal"
    assert not config.message_deduplicate
    assert not config.message_relevant_memory
    assert not config.message_aggregate_events


def test_action_context_memory_modes_preserve_or_replace_engine_explanation():
    memory = {"recent": [{"action_id": "look", "explanation": "Engine found a key.",
                          "contextual_description": "The key becomes a practical lead."}]}
    structured = memory_context({**memory, "action_context_mode": "structured"})
    both = memory_context({**memory, "action_context_mode": "both"})
    narrative = memory_context({**memory, "action_context_mode": "narrative"})
    assert structured["recent"][0]["explanation"] == "Engine found a key."
    assert "contextual_description" not in structured["recent"][0]
    assert both["recent"][0]["contextual_description"] == "The key becomes a practical lead."
    assert narrative["recent"][0]["explanation"] == "The key becomes a practical lead."
    assert "contextual_description" not in narrative["recent"][0]


def test_memory_tiers_limit_context_and_escalate_without_mutating_source():
    memory = {
        "recent": [{"action_id": f"action-{index}", "explanation": str(index)} for index in range(6)],
        "discoveries": ["key"], "known_entities": {"key": {"id": "key", "name": "Brass key"}},
        "event_summaries": [{"action_id": "look", "count": 1}],
        "enduring_facts": [{"fact": "The key opens the chest."}],
        "operator_conversation": [{"question": "Remember this", "answer": "Yes"}],
    }
    before = copy.deepcopy(memory)
    short = memory_context(memory, "short")
    medium = memory_context(memory, "medium")
    long = memory_context(memory, "long")
    assert short["memory_scope"] == "short"
    assert len(short["recent"]) == 3
    assert "known_entities" not in short and "enduring_facts" not in short
    assert medium["memory_scope"] == "medium"
    assert "known_entities" in medium and "enduring_facts" not in medium
    assert long["memory_scope"] == "long"
    assert "enduring_facts" in long and "operator_conversation" in long
    assert memory == before


def test_fact_shorthand_preserves_debt_direction_and_source_without_mutating_memory():
    payload = {"memory": {"facts": [{
        "type": "debt", "debtor": "Arin", "creditor": "Mara", "amount": 3,
        "currency": "silver", "source": "M-12", "day": 4, "confidence": "high",
        "status": "unpaid",
    }]}}
    before = copy.deepcopy(payload)
    encoded, diagnostics = encode_prompt_payload(payload, "fact-shorthand")
    fact = encoded["data"]["memory"]["facts"][0]["fact"]
    assert fact == ("DEBT(debtor=Arin,creditor=Mara,amount=3,currency=silver,"
                    "source=M-12,day=4,confidence=high,status=unpaid)")
    assert diagnostics["facts_encoded"] == 1
    assert payload == before


def test_shorthand_uses_self_describing_tables_and_round_trips():
    payload = {"memory": {"recent": [
        {"id": index, "action": "wait", "result": "UNCHANGED"} for index in range(4)
    ]}}
    encoded, diagnostics = encode_prompt_payload(payload, "shorthand")
    table = encoded["data"]["memory"]["recent"]["$table"]
    assert table["columns"] == ["action", "id", "result"]
    assert len(table["rows"]) == 4
    assert diagnostics["tables_encoded"] == 1
    assert diagnostics["fallback_reason"] is None


def test_compression_deduplicates_repeated_strings_and_aggregates_identical_events():
    repeated = "An unchanged blocker that is long enough to qualify for deduplication."
    payload = {
        "observation": {"village": {"events": [
            {"id": f"event-{index}", "minute": index, "code": "BLOCKED", "result": repeated}
            for index in range(4)
        ]}},
        "memory": {"facts": [repeated, repeated]},
    }
    encoded, diagnostics = encode_prompt_payload(
        payload, "compact", deduplicate=True, aggregate_events=True)
    events = encoded["data"]["obs"]["village"]["events"]
    assert len(events) == 1
    assert events[0]["count"] == 4
    assert events[0]["source_ids"] == [f"event-{index}" for index in range(4)]
    assert diagnostics["events_aggregated"] == 3
    assert diagnostics["deduplicated_values"] >= 1
    assert encoded["compression"]["references"][0] == repeated


def test_compression_preserves_json_schema_string_references():
    payload = {
        "schema": {"$defs": {"Choice": {"type": "string"}},
                   "$ref": "#/$defs/Choice"}
    }
    encoded, diagnostics = encode_prompt_payload(payload, "shorthand", deduplicate=True)
    assert diagnostics["fallback_reason"] is None
    assert encoded["data"]["schema"]["$ref"] == "#/$defs/Choice"


def test_relevant_memory_selection_keeps_recent_and_goal_related_facts():
    payload = {
        "goal": {"objective": "Repair the wagon at the forge"},
        "observation": {"self": {"actor_id": "smith"}, "location": {"id": "forge"}, "actions": []},
        "memory": {"facts": [
            {"id": str(index), "text": ("forge wagon repair" if index == 0 else f"unrelated memory {index}")}
            for index in range(40)
        ]},
    }
    encoded, diagnostics = encode_prompt_payload(payload, "normal", relevant_memory=True)
    retained = encoded["data"]["memory"]["facts"]
    assert any(item.get("id") == "0" for item in retained)
    assert {item["id"] for item in retained}.issuperset({str(index) for index in range(34, 40)})
    assert diagnostics["memory_entries_omitted"] == 16
