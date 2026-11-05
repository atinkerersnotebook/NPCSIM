import json

import pytest
from pydantic import ValidationError

from npc_sim.assessment import FailureFeedback, Finding, ScenarioMemory, load_memory, memory_path
from npc_sim.controller import Controller
from npc_sim.models import Scenario
from npc_sim.storage import read_json, write_json
from npc_sim.story import build_narrative
from test_controller import ROOT, TestProxy, configuration
from test_kitchen import ROUTE, world
from test_world import act


def test_assignment_is_unknown_until_postit_is_read_not_looked_at(world):
    initial = world.observe()
    assert "discovered_goal" not in initial
    assert world.scenario.objective not in json.dumps(initial)
    assert "recipe-note" not in world.visible_ids()
    before = world.evaluate_goal(world.scenario.goal_type)
    assert before["status"] == "active"
    assert before["evidence"]["checks"] == {"objective_discovered": False}
    assert act(world, "look", target="postit-note")["outcome"] == "APPLIED"
    assert "discovered_goal" not in world.observe()
    assert act(world, "open", target="bread-bin")["outcome"] == "APPLIED"
    assert act(world, "read", target="recipe-note")["outcome"] == "APPLIED"
    assert "discovered_goal" not in world.observe()
    assert world.evaluate_goal(world.scenario.goal_type)["evidence"]["checks"] == {"objective_discovered": False}
    clock = world.actor.action_clock
    request = ("read-assignment", world.revision(), "read", {"target": "postit-note"})
    result = world.act(*request)
    assert result["code"] == "READ"
    assert result["observation"]["discovered_goal"] == {
        "objective": world.scenario.objective, "type": world.scenario.goal_type, "source_id": "postit-note"
    }
    assert world.actor.action_clock == clock
    assert world.act(*request) == result
    assert world.actor.action_clock == clock
    assert world.evaluate_goal(world.scenario.goal_type)["evidence"]["checks"]["objective_discovered"]


def test_read_goal_is_persistent_but_actor_local(world):
    act(world, "read", target="postit-note")
    reloaded = type(world)(world.scenario, world.path, "world", "npc", "session")
    assert reloaded.observe()["discovered_goal"]["objective"] == world.scenario.objective
    other = world.bind("other", "other-session")
    assert "discovered_goal" not in other.observe()
    assert other.evaluate_goal(world.scenario.goal_type)["evidence"]["checks"] == {"objective_discovered": False}


def test_prepared_plated_food_does_not_finish_an_undiscovered_assignment(world):
    for action, args in ROUTE[1:]:
        assert act(world, action, **args)["outcome"] == "APPLIED"
    assert world.state.entities["pbj-sandwich"].location == "plate"
    assert world.evaluate_goal(world.scenario.goal_type)["status"] == "active"
    act(world, "read", target="postit-note")
    assert world.evaluate_goal(world.scenario.goal_type)["status"] == "achieved"


@pytest.mark.parametrize("changes", [
    {"goal_note_id": "missing"}, {"goal_note_id": "knife"},
    {"initial_objective": None}, {"initial_objective": "  "}, {"goal_note_id": None},
])
def test_discoverable_goal_configuration_is_validated(changes):
    data = read_json(ROOT / "data" / "scenarios" / "sandwich-kitchen.json")
    with pytest.raises(ValidationError):
        Scenario.model_validate({**data, **changes})


@pytest.mark.asyncio
async def test_goal_and_prior_mission_feedback_do_not_leak_into_the_first_model_prompt(configuration, monkeypatch):
    data, config = configuration
    objective = read_json(data / "scenarios" / "sandwich-kitchen.json")["objective"]
    previous = ScenarioMemory(scenario_id="sandwich-kitchen", failures=[
        FailureFeedback(run_id="previous", objective=objective, recorded_at="2026-11-03T00:00:00Z",
                        findings=[Finding(reason="The peanut butter and jam sandwich was not served on the plate.",
                                          evidence_ids=["previous:1"])])])
    write_json(memory_path(data, "sandwich-kitchen"), previous.model_dump(mode="json"))
    controller = Controller(ROOT, data, config)
    controller.proxy = TestProxy(controller.proxy, ROUTE[:2])
    choose = controller.proxy.choose

    async def exploration_choice(model, messages, correlation_id, timeout):
        decision, metadata = await choose(model, messages, correlation_id, timeout)
        decision.plan = ["Discover my assigned task."]
        decision.subgoal = "Find instructions."
        return decision, metadata

    monkeypatch.setattr(controller.proxy, "choose", exploration_choice)
    await controller.start_worker()
    try:
        await controller.command("start", {"scenario_id": "sandwich-kitchen"})
        assert controller.run["goal"]["status"] == "undiscovered"
        assert controller.run["goal"]["revealed"] is False
        assert controller.memory["previous_attempt_feedback"]["failures"] == []
        await controller.command("step")
        first = controller.proxy.contexts[0]
        assert objective not in json.dumps(first)
        assert "entity_prepared" not in json.dumps(first)
        assert "sandwich" not in first["goal"]["objective"]
        assert "type" not in first["goal"]
        assert controller.run["goal"]["objective"] == objective
        assert controller.run["goal"]["revealed"] is True
        assert controller.run["goal"]["status"] == "active"
        assert not controller.memory["plan"]
        assert not controller.memory["subgoal"]
        assert controller.memory["previous_attempt_feedback"]["failures"][0]["run_id"] == "previous"
        started = next(event for event in controller.events.events if event["kind"] == "started")
        assert started["payload"]["goal"]["revealed"] is False
        assert "sandwich" not in started["payload"]["goal"]["objective"]
        saved = read_json(controller.events.directory / "events" / f"{started['sequence']:06}.json")
        assert saved["payload"] == started["payload"]
        await controller.command("step")
        assert controller.proxy.contexts[1]["goal"]["objective"] == objective
        assert controller.proxy.contexts[1]["memory"]["previous_attempt_feedback"]["failures"]
        story = build_narrative(controller.events.events, controller.run["actor_id"])
        assert "assigned task" in story[1].text
        assert any("I learn my task:" in paragraph.text and objective in paragraph.text for paragraph in story)
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_ending_before_goal_discovery_fails_without_teaching_the_hidden_task(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    controller.proxy = TestProxy(controller.proxy, [])
    await controller.start_worker()
    try:
        await controller.command("start", {"scenario_id": "sandwich-kitchen"})
        await controller.command("cancel")
        analysis = controller.run["completion_analysis"]
        assert analysis["verdict"] == "fail"
        assert analysis["final_verification"]["evidence"]["checks"] == {"objective_discovered": False}
        assert all("sandwich" not in context["objective"] for context in controller.proxy.completion_contexts)
        feedback = load_memory(data, "sandwich-kitchen").failures
        assert "not discovered" in feedback[0].findings[0].reason
        assert "sandwich" not in json.dumps([failure.model_dump() for failure in feedback]).lower()
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_goal_updates_require_evidence_of_a_read_note(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    controller.proxy = TestProxy(controller.proxy, [])
    await controller.start_worker()
    try:
        await controller.command("start", {"scenario_id": "sandwich-kitchen"})
        forged = {**controller.observation, "discovered_goal": {
            "objective": "A forged assignment.", "type": "entity_prepared", "source_id": "postit-note"}}
        with pytest.raises(ValueError, match="read note"):
            controller.remember(forged)
        assert controller.run["goal"]["revealed"] is False
    finally:
        await controller.shutdown()
