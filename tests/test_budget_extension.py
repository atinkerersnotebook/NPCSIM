import copy
import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import npc_sim.app as application
from npc_sim.assessment import load_memory
from npc_sim.controller import Controller
from npc_sim.models import BudgetExtension, RunModeRequest
from npc_sim.proxy import Proxy
from npc_sim.storage import read_json, write_json
from test_controller import ROOT, SOLUTION, TestProxy, assessment_response, configuration


@pytest.mark.parametrize("body", [
    {}, {"run_id": "run"}, {"run_id": "", "run_until_complete": True},
    {"run_id": "run", "run_until_complete": "true"},
    {"run_id": "run", "run_until_complete": 1},
])
def test_run_mode_requires_explicit_valid_choice(body):
    with pytest.raises(ValidationError):
        RunModeRequest.model_validate(body)


@pytest.mark.asyncio
async def test_run_until_complete_bypasses_all_budgets_and_still_verifies_goal(configuration):
    data, config = configuration
    config.world.scenario = "escape-room"
    config.budgets.max_actions = config.budgets.max_model_calls = 1
    controller = Controller(ROOT, data, config)
    controller.proxy = TestProxy(controller.proxy, SOLUTION)
    await controller.start_worker()
    try:
        await controller.command("start")
        run_id = controller.run["id"]
        before = copy.deepcopy(controller.status())
        await controller.command("run_mode", {"run_id": run_id, "run_until_complete": True})
        assert controller.run["status"] == "ready"
        assert controller.status()["world"] == before["world"]
        assert controller.budgets.model_dump() == before["run"]["config"]["budgets"]
        controller.run["elapsed_seconds"] = config.budgets.max_seconds + 10
        for _ in SOLUTION:
            await controller.command("step")
        assert controller.run["status"] == "succeeded"
        assert controller.run["actions"] == controller.run["model_calls"] == len(SOLUTION)
        assert controller.run["elapsed_seconds"] > config.budgets.max_seconds
        assert controller.run["verification"]["status"] == "achieved"
        assert read_json(controller.events.directory / "run.json")["run_until_complete"] is True
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_run_mode_reopens_budget_exhaustion_and_restores_original_caps(configuration):
    data, config = configuration
    config.world.scenario = "escape-room"
    config.budgets.max_actions = 1
    controller = Controller(ROOT, data, config)
    controller.proxy = TestProxy(controller.proxy, SOLUTION[:2])
    await controller.start_worker()
    try:
        await controller.command("start")
        await controller.command("step")
        assert controller.run["status"] == "budget_exceeded"
        before = copy.deepcopy(controller.status())
        await controller.command("run_mode", {"run_id": controller.run["id"], "run_until_complete": True})
        assert controller.run["status"] == "paused"
        assert controller.status()["world"] == before["world"]
        assert controller.run["actions"] == controller.run["model_calls"] == 1
        await controller.command("step")
        assert controller.run["actions"] == 2
        await controller.command("run_mode", {"run_id": controller.run["id"], "run_until_complete": False})
        await controller.command("step")
        assert controller.run["status"] == "budget_exceeded"
        assert controller.run["actions"] == 2
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_unlimited_village_cycle_pauses_at_exact_midnight_and_rearms(configuration):
    data, config = configuration
    config.world.scenario = "millhaven-expanded"
    controller = Controller(ROOT, data, config)
    proxy = TestProxy(controller.proxy, [])
    controller.proxy = proxy
    await controller.start_worker()
    try:
        await controller.command("start")
        # Exercise non-default village IDs as well as the original scenario.
        controller.run["scenario_id"] = "millhaven-living"
        controller.run["status"] = "paused"
        observed = copy.deepcopy(controller.observation)
        observed["village"]["minute"] = 1380
        controller.observation = observed
        controller.emit("observation", observed)
        await controller.command("run_mode", {"run_id": controller.run["id"], "run_until_complete": True})
        assert controller.run["village_stop_minute"] == 1440
        controller.run["actions"] = controller.run["model_calls"] = 10000
        controller.started_clock = time.monotonic() - 100000
        assert await controller.verify() is False
        observed["village"]["minute"] = 1440
        controller.world.call = AsyncMock(return_value={"actor_id": None, "minute": 1440})
        controller.world.operation = AsyncMock(return_value=observed)
        await controller.command("step")
        assert controller.run["status"] == "paused"
        assert controller.run["village_minute"] == 1440
        assert controller.run["reason"].startswith("Reached midnight")
        assert "village_stop_minute" not in controller.run
        assert controller.run["model_calls"] == 10000
        assert not proxy.contexts
        controller.world.call.assert_awaited_once_with("world_next_village_actor", {"stop_at_minute": 1440})
        controller._arm_cycle_boundary()
        assert controller.run["village_stop_minute"] == 2880
        await controller.command("run_mode", {"run_id": controller.run["id"], "run_until_complete": False})
        assert "village_stop_minute" not in controller.run
    finally:
        await controller.shutdown(preserve_paused=True)


@pytest.mark.parametrize("extra", [
    {}, {"add_actions": -1}, {"add_actions": 1.5}, {"add_actions": True},
    {"add_model_calls": -1}, {"add_model_calls": "10"},
    {"add_seconds": -1}, {"add_seconds": float("inf")}, {"add_seconds": float("nan")},
    {"add_actions": 1, "unknown": 1},
])
def test_extension_rejects_invalid_additions(extra):
    with pytest.raises(ValidationError):
        BudgetExtension.model_validate({"run_id": "run", **extra})


@pytest.mark.asyncio
async def test_ready_scenario_extension_changes_only_frozen_run_limits(configuration):
    data, config = configuration
    config.world.scenario = "millhaven-expanded"
    controller = Controller(ROOT, data, config)
    proxy = TestProxy(controller.proxy, [])
    controller.proxy = proxy
    await controller.start_worker()
    try:
        await controller.command("start")
        before = copy.deepcopy(controller.status())
        budgets = controller.budgets.model_dump()
        result = await controller.command("extend_budget", {
            "run_id": controller.run["id"], "add_actions": 100,
            "add_model_calls": 200, "add_seconds": 300,
        })
        assert result["run"]["id"] == before["run"]["id"]
        assert result["run"]["status"] == "ready"
        assert result["world"] == before["world"]
        assert result["memory"] == before["memory"]
        assert result["config"] == before["config"]
        assert result["run"]["actions"] == result["run"]["model_calls"] == 0
        assert controller.budgets.max_actions == budgets["max_actions"] + 100
        assert controller.budgets.max_model_calls == budgets["max_model_calls"] + 200
        assert controller.budgets.max_seconds == budgets["max_seconds"] + 300
        assert not proxy.contexts and not proxy.completion_contexts
        assert read_json(controller.events.directory / "run.json")["config"]["budgets"] == controller.budgets.model_dump()
        assert controller.events.events[-1]["kind"] == "budget_extended"
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_exhausted_run_reopens_paused_without_actions_calls_or_stale_feedback(configuration):
    data, config = configuration
    config.world.scenario = "escape-room"
    config.budgets.max_actions = 1
    controller = Controller(ROOT, data, config)
    proxy = TestProxy(controller.proxy, SOLUTION[:2])
    controller.proxy = proxy
    await controller.start_worker()
    try:
        await controller.command("start")
        await controller.command("step")
        assert controller.run["status"] == "budget_exceeded"
        assert controller.started_clock is None
        controller.run["elapsed_seconds"] = controller.budgets.max_seconds
        assert controller.run["completion_analysis"]["verdict"] == "fail"
        assert load_memory(data, "escape-room").failures[0].run_id == controller.run["id"]
        before = copy.deepcopy(controller.status())
        world = controller.world
        with pytest.raises(ValueError, match="actions"):
            await controller.command("extend_budget", {"run_id": controller.run["id"], "add_model_calls": 20})
        assert controller.run == before["run"]
        result = await controller.command("extend_budget", {
            "run_id": controller.run["id"], "add_actions": 2, "add_seconds": 60})
        assert result["run"]["status"] == "paused"
        assert result["run"]["id"] == before["run"]["id"]
        assert result["run"]["elapsed_seconds"] == before["run"]["elapsed_seconds"]
        assert result["world"] == before["world"]
        assert controller.world is world
        assert result["run"]["actions"] == result["run"]["model_calls"] == 1
        assert controller.started_clock is None
        assert "completion_analysis" not in result["run"]
        assert result["run"]["previous_review_model_calls"] == 1
        assert result["run"]["goal"]["status"] == "active"
        expected_memory = before["memory"]
        expected_memory.pop("completion_feedback")
        assert result["memory"] == expected_memory
        assert not load_memory(data, "escape-room").failures
        assert len(proxy.contexts) == len(proxy.completion_contexts) == 1
        assert any(event["kind"] == "assessment_result" for event in controller.events.events)
        await controller.command("step")
        assert controller.run["status"] == "paused"
        assert controller.run["actions"] == controller.run["model_calls"] == 2
        assert controller.events.events[-1]["kind"] != "finished"
        assert [event["payload"]["action_id"] for event in controller.events.events
                if event["kind"] == "action_request"] == ["look", "open"]
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_call_limit_extension_retries_unacted_team_member(configuration):
    data, config = configuration
    config.world.scenario = "storm-signal"
    config.budgets.max_model_calls = 1
    controller = Controller(ROOT, data, config)
    proxy = TestProxy(controller.proxy, [
        ("release_lens", {"target": "optical-cache"}), ("take", {"target": "mast-weight"}),
    ])
    controller.proxy = proxy
    await controller.start_worker()
    try:
        await controller.command("start")
        controller.run["config"]["budgets"]["max_model_calls"] = 1
        actors = list(controller.agents)
        await controller.command("step")
        await controller.command("step")
        assert controller.run["status"] == "budget_exceeded"
        assert controller.run["actions"] == 1
        assert controller.run["pending_actor_id"] == actors[1]
        await controller.command("extend_budget", {"run_id": controller.run["id"], "add_model_calls": 2})
        await controller.command("step")
        assert controller.run["actor_id"] == actors[1]
        assert controller.run["actions"] == controller.run["model_calls"] == 2
        assert [context["observation"]["self"]["actor_id"] for context in proxy.contexts] == actors[:2]
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario,first_action,next_action", [
    ("storm-signal", ("release_lens", {"target": "optical-cache"}), ("take", {"target": "mast-weight"})),
    ("escape-room", SOLUTION[0], SOLUTION[1]),
])
async def test_budget_exhausted_world_reconnects_after_restart(configuration, scenario, first_action, next_action):
    data, config = configuration
    config.world.scenario = scenario
    config.budgets.max_actions = 1
    first = Controller(ROOT, data, config)
    first.proxy = TestProxy(first.proxy, [first_action])
    await first.start_worker()
    await first.command("start")
    first.run["config"]["budgets"]["max_actions"] = 1
    await first.command("step")
    first.patterns, first.replan_count = ["recorded-pattern"], 1
    first.checkpoint()
    before = copy.deepcopy(first.status())
    await first.shutdown()
    restored = Controller(ROOT, data, config)
    proxy = TestProxy(restored.proxy, [next_action])
    restored.proxy = proxy
    await restored.start_worker()
    try:
        assert restored.world is None
        assert restored.patterns == ["recorded-pattern"]
        scenario_path = restored.events.directory / "scenario.json"
        scenario = read_json(scenario_path)
        scenario_path.unlink()
        with pytest.raises(ValueError, match="frozen scenario"):
            await restored.command("extend_budget", {"run_id": before["run"]["id"], "add_actions": 2})
        assert restored.world is None
        write_json(scenario_path, scenario)
        state_path = data / "worlds" / before["run"]["world_id"] / "world-state.json"
        saved_world = read_json(state_path)
        state_path.unlink()
        with pytest.raises(ValueError, match="saved world"):
            await restored.command("extend_budget", {"run_id": before["run"]["id"], "add_actions": 2})
        assert not state_path.exists()
        write_json(state_path, saved_world)
        await restored.command("extend_budget", {"run_id": before["run"]["id"], "add_actions": 2})
        assert restored.world is not None
        assert restored.status()["world"] == before["world"]
        assert restored.run["id"] == before["run"]["id"]
        assert restored.run["elapsed_seconds"] == before["run"]["elapsed_seconds"]
        assert restored.replan_count == 1
        assert not proxy.contexts and not proxy.completion_contexts
        await restored.command("step")
        assert restored.run["actions"] == restored.run["model_calls"] == 2
        assert restored.run["actor_id"] == (list(restored.agents)[1] if restored.agents else before["run"]["actor_id"])
    finally:
        await restored.shutdown()


@pytest.mark.asyncio
async def test_exhausted_village_retries_same_scheduled_actor_without_advancing_again(configuration):
    data, config = configuration
    config.world.scenario = "millhaven-expanded"
    controller = Controller(ROOT, data, config)
    proxy = TestProxy(controller.proxy, [("village_wait", {"minutes": 5})])
    controller.proxy = proxy
    await controller.start_worker()
    try:
        await controller.command("start")
        controller.run["config"]["budgets"]["max_model_calls"] = 1
        controller.run["model_calls"] = 1
        await controller.command("step")
        assert controller.run["status"] == "budget_exceeded"
        actor_id = controller.run["actor_id"]
        assert controller.run["pending_actor_id"] == actor_id
        before = copy.deepcopy(controller.status()["world"])
        await controller.command("extend_budget", {"run_id": controller.run["id"], "add_model_calls": 2})
        assert controller.status()["world"] == before
        await controller.command("step")
        assert controller.run["actor_id"] == actor_id
        assert controller.run["actions"] == 1
        assert proxy.contexts[0]["observation"]["village"]["minute"] == before["village"]["minute"]
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_extension_rejects_stale_runs_and_other_terminal_outcomes(configuration):
    data, config = configuration
    config.world.scenario = "escape-room"
    controller = Controller(ROOT, data, config)
    controller.proxy = TestProxy(controller.proxy, [])
    await controller.start_worker()
    try:
        await controller.command("start")
        with pytest.raises(ValueError, match="changed"):
            await controller.command("extend_budget", {"run_id": "old-run", "add_model_calls": 1})
        await controller.command("cancel")
        for status in ("succeeded", "blocked", "stalled", "cancelled", "error"):
            controller.run["status"] = status
            before = copy.deepcopy(controller.run)
            with pytest.raises(ValueError, match="Only active or budget-exhausted"):
                await controller.command("extend_budget", {"run_id": before["id"], "add_actions": 1})
            assert controller.run == before
    finally:
        await controller.shutdown()


def test_budget_extension_api_validates_and_persists_without_model_calls(configuration, monkeypatch):
    data, config = configuration
    config.world.scenario = "escape-room"
    write_json(data / "config.json", config.model_dump(mode="json"))
    monkeypatch.setattr(application, "DATA", data)

    async def assess(self, model, messages, correlation_id, timeout):
        return assessment_response(messages), {}

    monkeypatch.setattr(Proxy, "assess", assess)
    with TestClient(application.app) as client:
        initial = client.post("/api/control/start", json={}).json()
        run_id = initial["run"]["id"]
        assert client.post("/api/adventure/reopen", json={}).status_code == 422
        assert client.post("/api/adventure/reopen", json={"run_id": run_id}).status_code == 409
        for body in ({}, {"run_id": run_id}, {"run_id": run_id, "add_actions": -1}):
            assert client.post("/api/budget/extend", json=body).status_code == 422
        assert client.post("/api/budget/extend", json={
            "run_id": "stale", "add_actions": 10}).status_code == 409
        response = client.post("/api/budget/extend", json={
            "run_id": run_id, "add_actions": 10, "add_model_calls": 25, "add_seconds": 60})
        assert response.status_code == 200
        result = response.json()
        assert result["run"]["config"]["budgets"]["max_model_calls"] == config.budgets.max_model_calls + 25
        assert result["world"] == initial["world"]
        assert result["run"]["status"] == "ready"
        assert result["run"]["model_calls"] == result["run"]["actions"] == 0
        assert result["config"] == initial["config"]
        for body in ({}, {"run_id": run_id, "run_until_complete": "true"}):
            assert client.post("/api/adventure/run-mode", json=body).status_code == 422
        assert client.post("/api/adventure/run-mode", json={
            "run_id": "stale", "run_until_complete": True}).status_code == 409
        response = client.post("/api/adventure/run-mode", json={
            "run_id": run_id, "run_until_complete": True})
        assert response.status_code == 200
        assert response.json()["run"]["run_until_complete"] is True
        assert response.json()["world"] == initial["world"]
        assert response.json()["run"]["status"] == "ready"
