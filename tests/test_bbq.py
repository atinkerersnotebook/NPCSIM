import math
import random
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from npc_sim import bbq
from npc_sim.contracts import action_result, goal_result, observation
from npc_sim.controller import Controller
from npc_sim.models import Decision, Scenario
from npc_sim.storage import read_json
from npc_sim.world import World
from test_controller import TestProxy, configuration


ROOT = Path(__file__).resolve().parent.parent


def scenario():
    return Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "bbq-pitmaster.json"))


def world(tmp_path):
    return World(scenario(), tmp_path / "world.json", "bbq", "npc", "session")


def act(w, heat=None, request_id=None):
    rid = request_id or uuid4().hex
    return action_result(w.act(rid, w.revision(),
                              "monitor_bbq" if heat is None else "adjust_bbq_heat",
                              {} if heat is None else {"heat_percent": heat}), rid, w.session_id)


def test_focused_observation_actions_and_decision_paced_time(tmp_path):
    w = world(tmp_path)
    obs = observation(w.observe())
    assert obs["bbq"]["temperature_f"] == obs["bbq"]["target_f"] == 225
    assert obs["bbq"]["heat_percent"] == 50
    assert obs["self"]["inventory"] == obs["self"]["needs"] == []
    assert {action["id"] for action in obs["actions"]} == {"adjust_bbq_heat", "monitor_bbq"}
    assert "seed" not in obs["bbq"] and "next_flare_seconds" not in obs["bbq"]
    assert w.state.clock is None
    for _ in range(3):
        w.observe()
        assert w.evaluate_goal("bbq_temperature_controlled")["status"] == "active"
    assert w.state.bbq.elapsed_seconds == 0
    assert act(w)["outcome"] == "APPLIED"
    assert w.state.bbq.elapsed_seconds == 30
    assert w.actor.action_clock == 1
    assert w.state.bbq.in_band_seconds == 30
    assert act(w, 100)["observation"]["bbq"]["temperature_f"] > 225
    hot = w.state.bbq.temperature_f
    assert act(w, 0)["observation"]["bbq"]["temperature_f"] < hot
    assert w.act(uuid4().hex, w.revision(), "go", {})["code"] == "ACTION_UNAVAILABLE"


def test_flares_cooling_idempotency_and_persistence(tmp_path):
    w = world(tmp_path)
    w.state.bbq.next_flare_seconds = 20
    before_revision = w.revision()
    rid = uuid4().hex
    result = act(w, request_id=rid)
    assert result["observation"]["bbq"]["flare_active"]
    assert result["observation"]["bbq"]["flare_count"] == 1
    assert result["observation"]["bbq"]["temperature_f"] > 240
    assert "flare-up" in result["explanation"]
    assert w.act(rid, before_revision, "monitor_bbq", {}) == result
    assert w.act(uuid4().hex, before_revision, "monitor_bbq", {})["code"] == "STALE_OBSERVATION"
    restored = world(tmp_path)
    assert restored.state.bbq == w.state.bbq
    assert restored.act(rid, before_revision, "monitor_bbq", {}) == result
    act(restored, 20)
    act(w, 20)
    assert not restored.observe()["bbq"]["flare_active"]
    assert restored.state.bbq.temperature_f < result["observation"]["bbq"]["temperature_f"]
    # Continuing either copy must produce the same future disturbances and readings.
    for _ in range(12):
        bbq.apply(w, 50)
        bbq.apply(restored, 50)
    assert restored.state.bbq == w.state.bbq


def test_final_two_minutes_allow_settling_without_new_flares(tmp_path):
    w = world(tmp_path)
    w.state.bbq.elapsed_seconds = 1680
    w.state.bbq.next_flare_seconds = 1681
    for _ in range(4):
        act(w)
    assert w.state.bbq.flare_count == 0
    assert w.state.bbq.elapsed_seconds == 1800


@pytest.mark.parametrize("arguments", [
    {}, {"heat_percent": -1}, {"heat_percent": 101}, {"heat_percent": "50"},
    {"heat_percent": True}, {"heat_percent": 50, "target_f": 225},
    {"heat_percent": math.nan}, {"heat_percent": math.inf},
])
def test_invalid_heat_does_not_advance_or_mutate_simulation(tmp_path, arguments):
    w = world(tmp_path)
    before = w.state.bbq.model_copy(deep=True)
    result = w.act(uuid4().hex, w.revision(), "adjust_bbq_heat", arguments)
    assert result["code"] == "INVALID_ARGUMENTS"
    assert result["outcome"] == "REJECTED"
    assert w.state.bbq == before


@pytest.mark.parametrize("seed", range(10))
def test_temperature_feedback_recovers_from_flares_and_finishes_after_30_minutes(tmp_path, seed):
    w = world(tmp_path)
    w.state.bbq.seed = seed
    w.state.bbq.next_flare_seconds = random.Random(seed).randint(180, 300)
    for _ in range(60):
        heat = max(0, min(100, 50 - 0.6 * (w.state.bbq.temperature_f - 225)))
        act(w, heat)
    result = goal_result(w.evaluate_goal("bbq_temperature_controlled"))
    assert result["status"] == "achieved", result["evidence"]
    assert result["evidence"]["elapsed_seconds"] == 1800
    assert result["evidence"]["in_band_seconds"] >= 1440
    assert result["evidence"]["in_band"]
    assert 4 <= result["evidence"]["flare_count"] <= 7
    assert len(result["evidence"]["history"]) == 61
    assert w.actions() == []
    assert act(w)["code"] == "ACTION_UNAVAILABLE"


def test_bad_temperature_control_fails_and_threshold_is_exact(tmp_path):
    w = world(tmp_path)
    for _ in range(60):
        act(w, 100)
    assert w.evaluate_goal("bbq_temperature_controlled")["status"] == "failed"
    w.state.bbq.temperature_f = 225
    w.state.bbq.in_band_seconds = 1439
    assert w.evaluate_goal("bbq_temperature_controlled")["status"] == "failed"
    w.state.bbq.in_band_seconds = 1440
    assert w.evaluate_goal("bbq_temperature_controlled")["status"] == "achieved"
    w.state.bbq.temperature_f = 235.01
    assert w.evaluate_goal("bbq_temperature_controlled")["status"] == "failed"
    w.state.bbq.temperature_f = 235
    assert w.evaluate_goal("bbq_temperature_controlled")["status"] == "achieved"


@pytest.mark.parametrize("change", [
    {"bbq": None}, {"goal_type": "actor_at_exit_location"},
    {"needs": {"food": {"enabled": True}}},
    {"body": {"description": "Actor", "capabilities": ["manipulate"], "capacity": 1}},
    {"agents": [{"id": "pitmaster", "role": "Pitmaster"}]},
    {"recipe_actions": {"adjust_bbq_heat": "Override"}},
])
def test_invalid_scenario_configuration(change):
    raw = scenario().model_dump()
    raw.update(change)
    with pytest.raises(ValidationError):
        Scenario.model_validate(raw)


def test_missing_persistent_state_is_explicit_error(tmp_path):
    w = world(tmp_path)
    w.state.bbq = None
    w.save()
    with pytest.raises(ValueError, match="missing its persistent BBQ state"):
        world(tmp_path)


class PitmasterProxy(TestProxy):
    async def choose(self, model, messages, correlation_id, timeout):
        gauge = self.contexts[-1]["observation"]["bbq"]
        heat = max(0, min(100, 50 - 0.6 * (gauge["temperature_f"] - gauge["target_f"])))
        return Decision(action_id="adjust_bbq_heat", arguments={"heat_percent": heat},
                        justification="Respond to the observed gauge temperature.",
                        expected_outcome="Keep the BBQ near 225 F."), {}


@pytest.mark.asyncio
async def test_controller_tends_over_mcp_and_archives_final_score(configuration):
    data, config = configuration
    config.world.scenario = "bbq-pitmaster"
    controller = Controller(ROOT, data, config)
    controller.proxy = PitmasterProxy(controller.proxy, [])
    await controller.start_worker()
    try:
        await controller.command("start")
        for _ in range(60):
            await controller.command("step")
        assert controller.run["status"] == "succeeded"
        assert controller.run["successful"] is True
        evidence = controller.run["verification"]["evidence"]
        assert evidence["elapsed_seconds"] == 1800
        assert evidence["in_band_seconds"] >= 1440
        saved = read_json(data / "runs" / controller.run["id"] / "run.json")
        assert saved["verification"]["evidence"] == evidence
        assert saved["successful"] is True
        assert "BBQ" in str(saved["completion_analysis"]["story"])
    finally:
        await controller.shutdown()
