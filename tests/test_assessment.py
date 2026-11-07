from contextlib import asynccontextmanager
import argparse
import asyncio
import json
import re

import httpx
import pytest

from npc_sim.assessment import Finding, StoryReview, grade, load_memory, memory_path
from npc_sim.controller import Controller
from npc_sim.evaluate import successful_episode
from npc_sim.models import Budgets
from npc_sim.storage import read_json, write_json
from npc_sim.story import Paragraph, build_narrative
from test_controller import ROOT, TestProxy, configuration
from test_kitchen import ROUTE


@pytest.mark.asyncio
async def test_batch_outage_records_ungraded_pause_without_hanging_or_reviewing(configuration, monkeypatch):
    import npc_sim.evaluate as evaluation

    data, config = configuration
    write_json(data / "config.json", config.model_dump(mode="json"))
    monkeypatch.setattr(evaluation, "__file__", str(data.parent / "npc_sim" / "evaluate.py"))
    controllers = []

    class OfflineProxy(TestProxy):
        async def choose(self, *args, **kwargs):
            raise httpx.ConnectError("connection refused")

    class OfflineController(Controller):
        def __init__(self, root, episode_data, episode_config):
            super().__init__(ROOT, episode_data, episode_config)
            self.proxy = OfflineProxy(self.proxy, [])
            controllers.append(self)

    monkeypatch.setattr(evaluation, "Controller", OfflineController)
    report = await asyncio.wait_for(evaluation.evaluate(argparse.Namespace(
        scenario="escape-room", model="test-model", concurrency=1, episodes=1, fixture_episodes=0)), 15)
    result = report["episodes"][0]
    assert result["status"] == "paused"
    assert result["successful"] is None
    assert result["actions"] == 0
    assert report["summary"][0]["successes"] == 0
    controller = controllers[0]
    assert controller.proxy.completion_contexts == []
    assert controller.run["status"] == "paused"
    assert read_json(controller.events.directory / "run.json")["status"] == "paused"
    assert controller.world.session is None


@asynccontextmanager
async def kitchen_attempt(configuration, route=ROUTE, proxy_type=TestProxy):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    controller.proxy = proxy_type(controller.proxy, route)
    await controller.start_worker()
    try:
        await controller.command("start", {"scenario_id": "sandwich-kitchen"})
        yield controller
    finally:
        await controller.shutdown()


async def execute(controller, route):
    for _ in route:
        await controller.command("step")


@pytest.mark.asyncio
async def test_automatic_pass_reviews_the_actual_story_and_freezes_execution_time(configuration):
    async with kitchen_attempt(configuration) as controller:
        await execute(controller, ROUTE)
        analysis = controller.run["completion_analysis"]
        assert controller.run["status"] == "succeeded"
        assert controller.run["successful"] is True
        assert analysis["status"] == "completed" and analysis["verdict"] == "pass"
        assert analysis["model_calls"] == 1
        assert controller.run["model_calls"] == len(ROUTE)
        supplied = controller.proxy.completion_contexts[0]
        assert analysis["story"] == supplied["story"]
        prose = " ".join(paragraph["text"] for paragraph in analysis["story"])
        assert "I serve the peanut butter and jam sandwich on the plate." in prose
        assert "378 kcal" in prose
        assert "Test fixture decision." in prose
        assert "requests" not in supplied["final_observation"]
        elapsed = controller.run["elapsed_seconds"]
        await controller.command("assess")
        assert controller.run["elapsed_seconds"] == elapsed
        assert load_memory(controller.data, "sandwich-kitchen").failures == []
        saved = read_json(controller.events.directory / "summary.json")
        assert saved["successful"] and saved["completion_analysis"]["verdict"] == "pass"


@pytest.mark.asyncio
async def test_unplated_sandwich_finishes_but_fails_and_teaches_the_next_attempt(configuration):
    data, config = configuration
    route = ROUTE[:-1]
    config.budgets.max_actions = len(route)
    async with kitchen_attempt(configuration, route) as controller:
        await execute(controller, route)
        original_id = controller.run["id"]
        assert controller.run["status"] == "budget_exceeded"
        assert controller.run["successful"] is False
        analysis = controller.run["completion_analysis"]
        assert analysis["verdict"] == "fail"
        assert analysis["final_verification"]["evidence"]["checks"] == {
            "prepared_item": True, "served_on_target": False, "objective_discovered": True
        }
        assert any("serving surface" in finding["reason"] for finding in analysis["findings"])
        feedback = load_memory(data, "sandwich-kitchen").failures
        assert len(feedback) == 1 and feedback[0].run_id == original_id
        assert controller.memory["completion_feedback"]["verdict"] == "fail"
        await controller.command("assess")
        assert len(load_memory(data, "sandwich-kitchen").failures) == 1
        controller.proxy = TestProxy(controller.proxy.real_proxy, [ROUTE[0]])
        await controller.command("reset")
        assert controller.memory["previous_attempt_feedback"]["failures"] == []
        assert "bread-a" not in controller.memory["known_entities"]
        assert "knife" not in controller.memory["discoveries"]
        await controller.command("step")
        supplied = controller.proxy.contexts[0]["memory"]
        assert supplied["previous_attempt_feedback"]["failures"] == []
        assert supplied["previous_attempt_feedback"]["kind"] == "previous_experience_not_current_discoveries"
        assert controller.memory["previous_attempt_feedback"]["failures"][0]["run_id"] == original_id
        await controller.command("start", {"scenario_id": "escape-room"})
        assert controller.memory["previous_attempt_feedback"]["failures"] == []


class OptimisticReviewer(TestProxy):
    async def assess(self, model, messages, correlation_id, timeout):
        import json
        payload = json.loads(messages[1]["content"])
        return StoryReview(verdict="pass", findings=[
            Finding(reason="The task looks complete.", evidence_ids=[payload["final_verification"]["event_id"]])]), {}


@pytest.mark.asyncio
async def test_world_recheck_vetoes_a_false_story_pass(configuration):
    _, config = configuration
    config.budgets.max_actions = len(ROUTE) - 1
    async with kitchen_attempt(configuration, ROUTE[:-1], OptimisticReviewer) as controller:
        await execute(controller, ROUTE[:-1])
        analysis = controller.run["completion_analysis"]
        assert analysis["story_review"]["verdict"] == "pass"
        assert analysis["verdict"] == "fail" and controller.run["successful"] is False
        assert all("looks complete" not in finding["reason"] for finding in analysis["findings"])


class OfflineReviewer(TestProxy):
    async def assess(self, *args, **kwargs):
        raise httpx.ConnectError("review proxy unavailable")


@pytest.mark.asyncio
async def test_review_error_is_not_success_or_a_fabricated_lesson_and_can_be_retried(configuration):
    _, config = configuration
    config.budgets.max_actions = len(ROUTE) - 1
    async with kitchen_attempt(configuration, ROUTE[:-1], OfflineReviewer) as controller:
        await execute(controller, ROUTE[:-1])
        assert controller.run["status"] == "budget_exceeded"
        assert controller.run["successful"] is None
        assert controller.run["completion_analysis"]["status"] == "error"
        assert "unreachable" in controller.run["completion_analysis"]["error"]
        assert load_memory(controller.data, "sandwich-kitchen").failures == []
        controller.proxy = TestProxy(controller.proxy.real_proxy, [])
        await controller.command("assess")
        assert controller.run["successful"] is False
        assert controller.run["completion_analysis"]["status"] == "completed"
        assert len(load_memory(controller.data, "sandwich-kitchen").failures) == 1


@pytest.mark.asyncio
async def test_restart_reassessment_uses_frozen_scenario_and_keeps_the_final_world(configuration):
    data, config = configuration
    async with kitchen_attempt(configuration) as controller:
        await execute(controller, ROUTE)
        original_id = controller.run["id"]
        state_path = controller.world.state_path
        clock = read_json(state_path)["actors"][controller.run["actor_id"]]["action_clock"]
    current = read_json(data / "scenarios" / "sandwich-kitchen.json")
    current["goal_location_id"] = "counter"
    write_json(data / "scenarios" / "sandwich-kitchen.json", current)
    restored = Controller(ROOT, data, config)
    restored.proxy = TestProxy(restored.proxy, [])
    await restored.start_worker()
    try:
        assert restored.run["id"] == original_id
        await restored.command("assess")
        assert restored.run["successful"] is True
        assert read_json(state_path)["entities"]["pbj-sandwich"]["location"] == "plate"
        assert read_json(state_path)["actors"][restored.run["actor_id"]]["action_clock"] == clock
    finally:
        await restored.shutdown()


@pytest.mark.asyncio
async def test_corrupt_learning_memory_does_not_replace_an_active_attempt(configuration):
    async with kitchen_attempt(configuration) as controller:
        original_id = controller.run["id"]
        write_json(memory_path(controller.data, "sandwich-kitchen"), {"schema_version": "unsupported"})
        with pytest.raises(ValueError):
            await controller.command("start", {"scenario_id": "sandwich-kitchen"})
        assert controller.run["id"] == original_id and controller.run["status"] == "ready"


def test_unknown_or_other_actor_evidence_is_rejected():
    story = [Paragraph(id="run:1", text="I have made a sandwich.")]
    review = StoryReview(verdict="fail", findings=[Finding(
        reason="The sandwich is not served.", evidence_ids=["other-actor:1"])])
    with pytest.raises(ValueError, match="outside"):
        grade(review, {"status": "active"}, "run:2", story)


def test_a_model_failure_can_differ_from_the_execution_outcome():
    review = StoryReview(verdict="fail", findings=[Finding(
        reason="The story does not establish the objective.", evidence_ids=["run:1"])])
    verdict, findings = grade(review, {"status": "achieved"}, "run:2",
                              [Paragraph(id="run:1", text="I ended my attempt.")])
    assert verdict == "fail" and findings == review.findings


def test_batch_evaluation_requires_a_pass_and_checks_exact_budgets():
    budgets = Budgets(max_actions=10, max_seconds=20)
    result = {"status": "succeeded", "successful": True, "actions": 10, "elapsed_seconds": 20,
              "completion_analysis": {"status": "completed", "verdict": "pass",
                                      "final_verification": {"status": "achieved"}}}
    assert successful_episode(result, budgets)
    for changed in ({"successful": False}, {"actions": 11}, {"elapsed_seconds": 20.001},
                    {"completion_analysis": {"status": "error"}}, {"completion_analysis": {
                        "status": "completed", "verdict": "fail", "final_verification": {"status": "achieved"}}}):
        assert not successful_episode({**result, **changed}, budgets)


def test_final_story_is_actor_scoped_and_does_not_narrate_expected_outcomes_as_facts():
    def event(sequence, kind, payload, actor="npc"):
        return {"id": f"run:{sequence}", "sequence": sequence, "kind": kind, "payload": payload,
                "actor_id": actor, "cycle": 1}
    initial = {
        "narrative": "A room.", "location": {"description": "A room."},
        "self": {"location_id": "room", "body": {"carrying": {"rule": "inventory_units", "limit": 1}}},
        "visible_entities": [{"id": "door", "name": "Door", "description": "A sturdy door."}],
    }
    first = event(1, "observation", initial)
    events = [first, first,
              event(2, "decision", {"action_id": "open", "justification": "I need to test the door.",
                                    "expected_outcome": "The door will open and I will escape."}),
              event(3, "action_request", {"action_id": "open", "arguments": {"target": "door"}}),
              event(4, "action_result", {"explanation": "The door is locked.", "observation": initial}),
              event(5, "observation", {**initial, "narrative": "Secret room."}, "other")]
    text = " ".join(paragraph.text for paragraph in build_narrative(events, "npc"))
    assert "I need to test the door. I try opening the door. The door is locked." in text
    assert "will escape" not in text and "Secret" not in text
    assert text.count("A room.") == 1


@pytest.mark.parametrize("units", [1, 2])
def test_narrative_omits_item_unit_costs_without_changing_observations(units):
    initial = {
        "narrative": "A workshop.",
        "self": {"location_id": "workshop", "body": {"carrying": {"rule": "inventory_units", "limit": 2}}},
        "visible_entities": [{
            "id": "arbor", "name": "Soft arbor", "description": "A sacrificial arbor.",
            "inventory_units": units,
        }],
    }
    events = [{"id": "run:1", "sequence": 1, "kind": "observation", "payload": initial,
               "actor_id": "npc", "cycle": 0}]
    original = json.dumps(events)
    text = " ".join(paragraph.text for paragraph in build_narrative(events, "npc"))
    assert "A sacrificial arbor." in text
    assert "I can carry at most 2 inventory units" in text
    assert "takes 1 inventory unit" not in text
    assert "takes 2 inventory units" not in text
    assert json.dumps(events) == original


@pytest.mark.parametrize("outcome", ["APPLIED", "REJECTED", "NO_CHANGE", None])
@pytest.mark.parametrize("team", [False, True])
def test_confirmed_actions_replace_attempts_but_failures_and_pending_actions_do_not(outcome, team):
    initial = {
        "narrative": "A workshop.",
        "self": {"location_id": "workshop", "body": {"carrying": {"rule": "inventory_units", "limit": 2}}},
        "visible_entities": [{"id": "zero-coupon", "name": "Zero coupon", "description": "A reference coupon."}],
        **({"role": "A technician."} if team else {}),
    }
    events = [
        {"id": f"run:{index}", "sequence": index, "kind": kind, "payload": payload,
         "actor_id": "otter", "cycle": 1}
        for index, (kind, payload) in enumerate([
            ("observation", initial),
            ("decision", {"action_id": "take", "justification": "I need the coupon."}),
            ("action_request", {"action_id": "take", "arguments": {"target": "zero-coupon"}}),
        ], 1)
    ]
    if outcome is not None:
        events.append({"id": "run:4", "sequence": 4, "kind": "action_result", "actor_id": "otter",
                       "cycle": 1, "payload": {"outcome": outcome, "observation": initial,
                       "explanation": "You pick up the zero coupon." if outcome == "APPLIED" else "The coupon is out of reach."}})
    original = json.dumps(events)
    text = " ".join(paragraph.text for paragraph in build_narrative(events, "otter"))
    assert ("Otter needs the coupon." if team else "I need the coupon.") in text
    if outcome == "APPLIED":
        assert ("Otter picked up the zero coupon." if team else "I picked up the zero coupon.") in text
        assert "try to pick" not in text and "tries to pick" not in text
    else:
        assert ("Otter tries to pick up the zero coupon." if team else "I try to pick up the zero coupon.") in text
        assert "picked up" not in text
    assert json.dumps(events) == original


def test_team_narrative_names_the_actor_and_uses_we_only_for_shared_success():
    initial = {
        "role": "A swimmer.", "narrative": "A riverbank.",
        "self": {"location_id": "bank", "body": {"carrying": {"rule": "item_count", "limit": 1}}},
        "visible_entities": [{"id": "rope", "name": "Rope", "description": "A rope."}],
    }

    def event(sequence, kind, payload, actor="otter"):
        return {"id": f"run:{sequence}", "sequence": sequence, "kind": kind, "payload": payload,
                "actor_id": actor, "cycle": 1}

    events = [
        event(1, "observation", initial),
        event(2, "observation", {**initial, "narrative": "Secret workshop."}, "fox"),
        event(3, "started", {"goal": {"objective": "Cross together."}}),
        event(4, "decision", {"action_id": "take", "justification": "I need my rope. We must cross together."}),
        event(5, "action_request", {"action_id": "take", "arguments": {"target": "rope"}}),
        event(6, "action_result", {
            "explanation": "You pick up the rope. You have room in your inventory.", "observation": initial}),
        event(7, "finished", {"status": "succeeded"}),
    ]
    text = " ".join(paragraph.text for paragraph in build_narrative(events, "otter"))
    assert "A riverbank." in text
    assert "Otter can carry at most 1 item" in text
    assert "Our goal is to cross together." in text
    assert "Otter needs Otter's rope. We must cross together. Otter tries to pick up the rope." in text
    assert "Otter picks up the rope. Otter has room in Otter's inventory." in text
    assert "we have achieved our goal" in text
    assert "Secret" not in text
    assert not re.search(r"\b(I|my|My|You|you)\b", text)


def test_complete_room_description_is_not_prefixed_as_a_noun_phrase():
    description = "A broken lift sits below a high balcony. A heavy beam lies nearby."
    initial = {
        "narrative": description,
        "self": {"location_id": "courtyard", "body": {"carrying": {"rule": "item_count", "limit": 1}}},
        "visible_entities": [],
    }
    events = [{"id": "run:1", "sequence": 1, "kind": "observation", "payload": initial,
               "actor_id": "raven", "cycle": 0}]
    text = build_narrative(events, "raven")[0].text
    assert text.startswith(description)
    assert "I am in a broken lift" not in text


@pytest.mark.parametrize("applied", [True, False])
def test_cooperative_story_uses_plain_prose_without_changing_action_evidence(applied):
    initial = {
        "role": "An underwater engineer.", "narrative": "A signal yard.",
        "self": {"location_id": "yard", "body": {"carrying": {"rule": "inventory_units", "limit": 1}}},
        "visible_entities": [{"id": "wiring-box", "name": "Wet beacon wiring box", "description": "A wet wiring box."}],
        "skill_challenges": [{"id": "repair_wiring", "target": "wiring-box",
                              "description": "Install the insulated wire coil and safely reconnect the wet beacon wiring."}],
    }
    request = {"action_id": "repair_wiring", "arguments": {"target": "wiring-box"}}
    explanation = ("You use water_engineering: Install the insulated wire coil and safely reconnect the wet beacon wiring."
                   if applied else "Prior work still needed: mount_lens.")
    events = [
        {"id": f"run:{index}", "sequence": index, "kind": kind, "payload": payload, "actor_id": "otter", "cycle": 1}
        for index, (kind, payload) in enumerate([
            ("observation", initial),
            ("decision", {"action_id": "repair_wiring", "justification": "otter can repair_wiring at wiring-box."}),
            ("action_request", request),
            ("action_result", {"outcome": "APPLIED" if applied else "REJECTED",
                               "explanation": explanation, "observation": initial}),
        ], 1)
    ]
    text = " ".join(paragraph.text for paragraph in build_narrative(events, "otter"))
    assert ("Otter tries to install the insulated wire coil" in text) is not applied
    assert "Otter can repair wiring at wet beacon wiring box." in text
    assert not re.search(r"repair_wiring|water_engineering|mount_lens|wiring-box|[{}]", text)
    if applied:
        assert "Otter uses underwater engineering to install" in text
    else:
        assert "Prior work still needed: mount lens." in text
        assert "Otter uses underwater engineering" not in text
    assert request["arguments"] == {"target": "wiring-box"}
    assert events[-1]["payload"]["explanation"] == explanation


def test_unknown_action_has_a_readable_attempt_instead_of_json():
    from npc_sim.story import intent

    text = intent("adjust_pressure", {"target": "pressure-valve", "level": 3},
                  {"pressure-valve": "Brass pressure valve"})
    assert text == "I try to adjust pressure at the brass pressure valve with level set to 3."


def test_team_adventure_is_chronological_without_sharing_private_names():
    def event(sequence, kind, payload, actor):
        return {"id": f"run:{sequence}", "sequence": sequence, "kind": kind, "payload": payload,
                "actor_id": actor, "cycle": sequence}

    def observed(entity, name):
        return {
            "role": "A teammate.", "narrative": "A room.",
            "self": {"location_id": "room", "body": {"carrying": {"rule": "inventory_units", "limit": 1}}},
            "visible_entities": [{"id": entity, "name": name, "description": "A useful object."}],
        }

    events = [
        event(1, "observation", observed("key", "Brass key"), "raven"),
        event(2, "observation", observed("box", "Supply box"), "badger"),
        event(3, "observation", observed("rope", "Rope"), "otter"),
        event(4, "action_request", {"action_id": "take", "arguments": {"target": "key"}}, "raven"),
        event(5, "action_request", {"action_id": "take", "arguments": {"target": "key"}}, "badger"),
        event(6, "action_request", {"action_id": "take", "arguments": {"target": "rope"}}, "otter"),
    ]
    story = build_narrative(list(reversed(events)) + [events[0]])
    assert [paragraph.id for paragraph in story] == [event["id"] for event in events]
    assert story[3].text == "Raven tries to pick up the brass key."
    assert story[4].text == "Badger tries to pick up the key."
    assert story[5].text == "Otter tries to pick up the rope."
    for event in events[:3]:
        event["payload"].update(opening=True, narrative="Three friends face a storm.")
    assert sum(paragraph.text == "Three friends face a storm." for paragraph in build_narrative(events)) == 1
