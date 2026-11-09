import copy
from unittest.mock import AsyncMock

import pytest

from npc_sim.assessment import load_memory
from npc_sim.controller import Controller
from npc_sim.models import ReopenRequest, VillageDayPolicy, VillageEvent, VillageMeal
from npc_sim.storage import EventStore, read_json, write_json
from npc_sim.world import World
from npc_sim import village_turns
from test_controller import ROOT, TestProxy, configuration
from test_village_expanded import scenario
from test_village_remaining import cast


@pytest.fixture
def completed(cast):
    world = cast["torvin"]
    state = world.state.village
    state.minute = 1380
    state.shovel_recovered = state.well_dug = state.repaired = state.wagon.repaired = True
    state.bread_deliveries = 1
    state.role_contributions = {rid: 1 for rid in cast}
    for rid, person in state.residents.items():
        resident = world.scenario.village.residents[rid]
        world.state.actors[rid].location_id = resident.home
        person.life.sleeping = True
        person.life.sleep_until = 1440 + resident.wake_minute
        person.life.meals["0:daily"] = VillageMeal(kind="daily", due=1140, status="eaten", source="tavern:meals")
    for rid, action in [("ada", "village_grow_hay"), ("iona", "village_gather_herbs"),
                        ("rowan", "village_fell_logs"), ("ada", "village_fertilize")]:
        state.events.append(VillageEvent(minute=600, actor=rid, action=action,
                                         target=world.scenario.village.residents[rid].work,
                                         result="Completed real work.", witnesses=[rid]))
    state.residents["iona"].life.tea_day = 0
    return world


def test_full_day_completes_only_with_meals_home_sleep_animals_and_resources(completed):
    result = completed.evaluate_goal("village_restored")
    assert result["status"] == "achieved", result
    assert result["evidence"]["completion_scope"] == "full_village_day"
    assert all(result["evidence"]["checks"].values())
    completed.save()
    restored = World(completed.scenario, completed.path, completed.state.world_id,
                     completed.actor_id, completed.session_id)
    assert restored.evaluate_goal("village_restored") == result


def test_repaired_village_cannot_finish_at_1430_even_if_everyone_is_home(completed):
    completed.state.village.minute = 870
    result = completed.evaluate_goal("village_restored")
    checks = result["evidence"]["checks"]
    assert all(checks[key] for key in ["shovel_recovered", "well_dug", "mill_repaired",
                                      "bread_supply_resumed", "every_resident_contributed",
                                      "committed_batches_finished", "working_wagon"])
    assert result["status"] == "active"
    assert checks["everyone_home"] and checks["everyone_asleep"]
    assert not checks["evening_reached"] and not checks["everyone_fed"]


@pytest.mark.parametrize("missing", [
    "meal", "home", "sleep", "horse_home", "horse_hitched", "horse_led",
    "wagon_route", "wagon_driver", "timber_route",
    "village_grow_hay", "village_gather_herbs", "village_fell_logs", "village_fertilize", "tea",
])
def test_incomplete_requirement_keeps_village_active(completed, missing):
    state = completed.state.village
    if missing == "meal":
        state.residents["iona"].life.meals["0:daily"].status = "missed"
    elif missing == "home":
        completed.state.actors["torvin"].location_id = "forge"
    elif missing == "sleep":
        state.residents["torvin"].life.sleeping = False
    elif missing == "horse_home":
        completed.state.entities["work-horse"].location = "farm"
    elif missing == "horse_hitched":
        state.wagon.hitched = True
    elif missing == "horse_led":
        state.horse_leader = "bren"
    elif missing == "wagon_route":
        state.wagon.automatic = True
    elif missing == "wagon_driver":
        state.wagon.driver = "nella"
    elif missing == "timber_route":
        state.residents["rowan"].timber_route = True
    elif missing == "tea":
        state.residents["iona"].life.tea_day = -1
    else:
        state.events = [event for event in state.events if event.action != missing]
    result = completed.evaluate_goal("village_restored")
    assert result["status"] == "active", (missing, result)
    assert not all(result["evidence"]["checks"].values())


def test_after_midnight_uses_previous_evening_and_allows_later_daily_recovery(completed):
    state = completed.state.village
    state.minute = 1500
    assert completed.evaluate_goal("village_restored")["status"] == "achieved"
    state.minute = 2820
    for rid, person in state.residents.items():
        person.life.sleep_until += 1440
        person.life.meals["0:daily"].status = "missed"
    assert completed.evaluate_goal("village_restored")["status"] == "active"
    for person in state.residents.values():
        person.life.meals["1:daily"] = VillageMeal(kind="daily", due=2580, status="eaten", source="tavern:meals")
    assert completed.evaluate_goal("village_restored")["status"] == "achieved"


@pytest.mark.parametrize("breakfast", ["missing", "missed"])
def test_breakfast_is_optional_for_daily_completion(completed, breakfast):
    state = completed.state.village
    state.day_policy = VillageDayPolicy(breakfast=True, demand_led_completion=True)
    if breakfast == "missed":
        for person in state.residents.values():
            person.life.meals["0:breakfast"] = VillageMeal(
                kind="breakfast", due=480, status="missed")
    before = copy.deepcopy(state.model_dump(mode="json"))
    result = completed.evaluate_goal("village_restored")
    assert result["status"] == "achieved", result
    assert result["evidence"]["daily_requirements_recoverable"] is True
    assert state.model_dump(mode="json") == before


@pytest.mark.asyncio
@pytest.mark.parametrize("restart", [False, True])
async def test_reopen_recoverable_daily_ai_defeat_preserves_progress(configuration, restart):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    controller.proxy = TestProxy(controller.proxy, [("village_wait", {"minutes": 5})])
    await controller.start_worker()
    try:
        await controller.command("start", {"scenario_id": "millhaven-living"})
        await controller.command("step")
        controller.dungeon_master_ruling(
            "episode", "defeat", "An earlier supper was missed.",
            [controller.events.events[-1]["id"]], "ai")
        controller.finish("blocked", "Dungeon Master rules that the team must admit defeat.")
        await controller.command("assess")
        before = copy.deepcopy(controller.status())
        if restart:
            await controller.shutdown()
            controller = Controller(ROOT, data, config)
            controller.proxy = TestProxy(controller.proxy, [])
            await controller.start_worker()
        proxy = controller.proxy
        calls = (len(proxy.contexts), len(proxy.completion_contexts))
        await controller.command("reopen", {"run_id": before["run"]["id"], "until_midnight": True})
        assert controller.status()["world"] == before["world"]
        assert controller.run["status"] == "paused"
        assert controller.run["goal"]["status"] == "active"
        assert all(agent["goal"]["status"] == "active" for agent in controller.agents.values())
        for key in ("id", "world_id", "actions", "model_calls", "elapsed_seconds"):
            assert controller.run[key] == before["run"][key]
        assert controller.run["village_stop_minute"] == (
            before["world"]["village"]["minute"] // 1440 + 1) * 1440
        assert (len(proxy.contexts), len(proxy.completion_contexts)) == calls
    finally:
        await controller.shutdown(preserve_paused=True)


@pytest.mark.asyncio
async def test_reopen_does_not_override_authoritative_world_defeat(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    controller.proxy = TestProxy(controller.proxy, [])
    await controller.start_worker()
    try:
        await controller.command("start", {"scenario_id": "millhaven-living"})
        controller.dungeon_master_ruling(
            "episode", "defeat", "Authoritative world failure.",
            [controller.events.events[-1]["id"]], "world")
        controller.finish("blocked", "Authoritative world failure.")
        await controller.command("assess")
        before = copy.deepcopy(controller.run)
        with pytest.raises(ValueError, match="Only a prematurely"):
            await controller.command("reopen", {"run_id": before["id"]})
        assert controller.run == before
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("restart,acted", [(False, False), (True, False), (False, True)])
async def test_reopen_premature_completion_preserves_world_and_does_not_run_models(configuration, restart, acted):
    data, config = configuration
    value = scenario()
    value.village.start_minute = 480
    write_json(data / "scenarios" / "millhaven-expanded.json", value.model_dump(mode="json"))
    config.world.scenario = "millhaven-expanded"
    controller = Controller(ROOT, data, config)
    proxy = TestProxy(controller.proxy, [("village_wait", {"minutes": 5})] * 2)
    controller.proxy = proxy
    await controller.start_worker()
    try:
        await controller.command("start")
        if acted:
            await controller.command("step")
        controller.finish("succeeded", "Legacy project-only completion.")
        await controller.command("assess")
        assert controller.run["completion_analysis"]["verdict"] == "fail"
        before = copy.deepcopy(controller.status())
        if restart:
            await controller.shutdown()
            controller = Controller(ROOT, data, config)
            proxy = TestProxy(controller.proxy, [("village_wait", {"minutes": 5})])
            controller.proxy = proxy
            await controller.start_worker()
        calls = len(proxy.completion_contexts)
        decision_calls = len(proxy.contexts)
        with pytest.raises(ValueError, match="changed"):
            await controller.command("reopen", {"run_id": "stale"})
        await controller.command("reopen", {"run_id": before["run"]["id"]})
        assert controller.status()["world"] == before["world"]
        assert controller.run["id"] == before["run"]["id"]
        assert controller.run["status"] == "paused"
        assert controller.run["actions"] == before["run"]["actions"]
        assert controller.run["model_calls"] == before["run"]["model_calls"]
        assert controller.run["elapsed_seconds"] == before["run"]["elapsed_seconds"]
        assert controller.run["goal"]["status"] == "active"
        assert controller.run["verification"]["status"] == "active"
        assert "completion_analysis" not in controller.run
        assert controller.run["previous_review_model_calls"] == 1
        assert len(proxy.completion_contexts) == calls and len(proxy.contexts) == decision_calls
        assert not load_memory(data, "millhaven-expanded").failures
        assert "completion_feedback" not in controller.memory
        assert controller.events.events[-1]["kind"] == "adventure_reopened"
        assert any(event["kind"] == "assessment_result" for event in controller.events.events)
        saved = copy.deepcopy(controller.run)
        with pytest.raises(ValueError, match="Only a prematurely"):
            await controller.command("reopen", {"run_id": before["run"]["id"]})
        assert controller.run == saved
        await controller.command("step")
        assert (controller.run["actor_id"] == before["run"]["actor_id"]) is not acted
        assert controller.run["actions"] == before["run"]["actions"] + 1
        assert controller.run["model_calls"] == before["run"]["model_calls"] + 1
    finally:
        await controller.shutdown(preserve_paused=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,scope", [
    ("achieved", "full_village_day"), ("failed", "full_village_day"), ("active", "other"),
])
async def test_reopen_never_changes_completed_or_unrelated_worlds(configuration, status, scope):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    controller.run = {
        "id": "run", "status": "succeeded", "goal": {"type": "village_restored"},
        "completion_analysis": {"status": "completed", "verdict": "fail"},
        "config": config.model_dump(mode="json"),
    }
    controller.events = EventStore(data / "test-run", "run")
    controller.world = AsyncMock()
    controller.world.operation.return_value = {
        "status": status, "reason": "Fixture verification.", "observation_revision": "obs-1",
        "evidence": {"completion_scope": scope},
    }
    before = copy.deepcopy(controller.run)
    with pytest.raises(ValueError, match="does not confirm"):
        await controller._command("reopen", {"run_id": "run"})
    assert controller.run == before
    assert not controller.events.events


@pytest.mark.asyncio
@pytest.mark.parametrize("restart", [False, True])
async def test_recover_committed_action_timeout_without_replaying_or_paying_again(configuration, monkeypatch, restart):
    data, config = configuration
    value = scenario()
    value.village.start_minute = 480
    value.agents[0].start_location = value.village.residents["torvin"].work
    write_json(data / "scenarios" / "millhaven-expanded.json", value.model_dump(mode="json"))
    config.world.scenario = "millhaven-expanded"
    controller = Controller(ROOT, data, config)
    controller.proxy = TestProxy(controller.proxy, [("village_wait", {"minutes": 5})] * 2)
    await controller.start_worker()
    try:
        await controller.command("start")
        operation = controller.world.operation

        async def lose_response(name, arguments, timeout=15):
            result = await operation(name, arguments, timeout)
            if name == "act":
                raise TimeoutError
            return result

        with monkeypatch.context() as patch:
            patch.setattr(controller.world, "operation", lose_response)
            await controller.command("step")
        assert controller.run["status"] == "error"
        assert controller.run["reason"] == "TimeoutError"
        pending = next(event for event in reversed(controller.events.events) if event["kind"] == "action_request")
        assert not any(event["kind"] == "action_result" for event in controller.events.events)
        before = copy.deepcopy(controller.status())
        if restart:
            await controller.shutdown()
            controller = Controller(ROOT, data, config)
            controller.proxy = TestProxy(controller.proxy, [("village_wait", {"minutes": 5})])
            await controller.start_worker()
        await controller.command("reopen", {"run_id": before["run"]["id"], "until_midnight": True})
        assert controller.status()["world"] == before["world"]
        assert controller.run["status"] == "paused"
        for key in ("id", "world_id", "actions", "model_calls", "elapsed_seconds", "cycles"):
            assert controller.run[key] == before["run"][key]
        results = [event for event in controller.events.events if event["kind"] == "action_result"
                   and event["payload"]["request_id"] == pending["payload"]["request_id"]]
        assert len(results) == 1
        assert results[0]["payload"]["outcome"] == "APPLIED"
        assert controller.run["village_stop_minute"] == 1440
        assert controller.run.get("pending_actor_id") is None
        await controller.command("step")
        assert controller.run["actions"] == before["run"]["actions"] + 1
        assert controller.run["model_calls"] == before["run"]["model_calls"] + 1
    finally:
        await controller.shutdown(preserve_paused=True)


@pytest.mark.parametrize("damage", [
    "missing", "arguments", "session", "cycle", "revision", "multiple_pending", "world", "recorded_conflict"
])
def test_committed_timeout_proof_refuses_missing_or_conflicting_evidence(configuration, cast, damage):
    data, config = configuration
    world = cast["torvin"]
    request = {"request_id": "confirmed", "expected_observation_revision": world.revision(),
               "action_id": "look", "arguments": {"target": "anvil"}}
    result = world.act(request["request_id"], request["expected_observation_revision"],
                       request["action_id"], request["arguments"])
    controller = Controller(ROOT, data, config)
    controller.run = {"actor_id": world.actor_id, "session_id": world.session_id,
                      "world_id": world.state.world_id, "cycles": 1}
    controller.events = EventStore(data / "proof-test", "run")
    event = {"id": "run:1", "kind": "action_request", "actor_id": world.actor_id,
             "session_id": world.session_id, "cycle": 1, "payload": copy.deepcopy(request)}
    controller.events.events = [event]
    saved = world.state.model_copy(deep=True)
    if damage == "missing":
        saved.requests = {}
        saved.request_history_id = None
    elif damage == "arguments":
        event["payload"]["arguments"]["target"] = "different"
    elif damage == "session":
        event["session_id"] = "different"
    elif damage == "cycle":
        event["cycle"] = 2
    elif damage == "revision":
        saved.revision += 1
    elif damage == "multiple_pending":
        extra = copy.deepcopy(event)
        extra["payload"]["request_id"] = "another-pending-action"
        controller.events.events.append(extra)
    elif damage == "world":
        controller.run["world_id"] = "different"
    else:
        altered = copy.deepcopy(result)
        altered["explanation"] = "Different result"
        controller.events.events.append({
            "kind": "action_result", "actor_id": world.actor_id, "payload": altered})
    before = world.snapshot()
    with pytest.raises(ValueError, match="confirmed free-action boundary or durable action result"):
        controller._confirmed_timeout_action(saved, world.path)
    assert world.snapshot() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("restart", [False, True])
async def test_recover_timeout_preserves_free_turn_and_stops_at_midnight(configuration, restart):
    data, config = configuration
    value = scenario()
    value.village.start_minute = 480
    value.agents[0].start_location = value.village.residents["torvin"].work
    write_json(data / "scenarios" / "millhaven-expanded.json", value.model_dump(mode="json"))
    config.world.scenario = "millhaven-expanded"
    controller = Controller(ROOT, data, config)
    proxy = TestProxy(controller.proxy, [("look", {"target": "anvil"}), ("village_wait", {"minutes": 5})])
    controller.proxy = proxy
    await controller.start_worker()
    try:
        await controller.command("start")
        await controller.command("step")
        assert controller.events.events[-1]["kind"] == "verification"
        assert next(event for event in reversed(controller.events.events)
                    if event["kind"] == "action_result")["free_action"]
        reason = "Timed out while waiting for response to ClientRequest. Waited 15.0 seconds."
        controller.emit("error", {"kind": "McpError", "message": reason})
        controller.finish("error", reason)
        await controller.command("assess")
        before = copy.deepcopy(controller.status())
        if restart:
            await controller.shutdown()
            controller = Controller(ROOT, data, config)
            proxy = TestProxy(controller.proxy, [("village_wait", {"minutes": 5})])
            controller.proxy = proxy
            await controller.start_worker()
        calls = (len(proxy.contexts), len(proxy.completion_contexts))
        await controller.command("reopen", {"run_id": before["run"]["id"], "until_midnight": True})
        assert controller.status()["world"] == before["world"]
        assert controller.run["status"] == "paused"
        assert controller.run["pending_actor_id"] == "torvin"
        assert controller.run["village_stop_minute"] == 1440
        for key in ["id", "world_id", "actions", "model_calls", "elapsed_seconds", "config"]:
            assert controller.run[key] == before["run"][key]
        assert (len(proxy.contexts), len(proxy.completion_contexts)) == calls
        assert controller.events.events[-1]["payload"]["previous_status"] == "error"
        await controller.command("step")
        assert controller.run["actor_id"] == "torvin"
        assert controller.run["actions"] == before["run"]["actions"] + 1

        # A scheduler boundary must pause before selecting or paying for another actor.
        observed = copy.deepcopy(controller.observation)
        observed["village"]["minute"] = 1440
        controller.world.call = AsyncMock(return_value={"actor_id": None, "minute": 1440})
        controller.world.operation = AsyncMock(return_value=observed)
        calls = controller.run["model_calls"]
        await controller.command("step")
        assert controller.run["status"] == "paused"
        assert controller.run["reason"] == "Reached midnight (00:00). Continue when ready."
        assert "village_stop_minute" not in controller.run
        assert controller.run["model_calls"] == calls
        controller.world.call.assert_awaited_once_with("world_next_village_actor", {"stop_at_minute": 1440})
    finally:
        await controller.shutdown(preserve_paused=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe", ["pending_request", "paid_turn", "different_actor"])
async def test_timeout_recovery_refuses_uncertain_turns(configuration, cast, unsafe):
    data, config = configuration
    world = cast["torvin"]
    world.state.village.free_turn_actor = "torvin" if unsafe != "different_actor" else "bren"
    write_json(data / "worlds" / world.state.world_id / "world-state.json", world.state.model_dump(mode="json"))
    controller = Controller(ROOT, data, config)
    controller.run = {
        "id": "run", "world_id": world.state.world_id, "status": "error",
        "reason": "Timed out while waiting for response to ClientRequest. Waited 15.0 seconds.",
        "goal": {"type": "village_restored"}, "config": config.model_dump(mode="json"),
    }
    controller.agents = {"torvin": {}, "bren": {}}
    controller.events = EventStore(data / "test-run", "run")
    controller.events.events = [{
        "kind": "action_result", "actor_id": "torvin", "free_action": unsafe != "paid_turn",
        "payload": {"request_id": "confirmed"},
    }]
    if unsafe == "pending_request":
        controller.events.events.append({"kind": "action_request", "payload": {"request_id": "uncertain"}})
    before = copy.deepcopy(controller.run)
    with pytest.raises(ValueError, match="confirmed free-action boundary"):
        await controller.reopen(ReopenRequest(run_id="run"))
    assert controller.run == before
    assert controller.world is None


def test_midnight_scheduler_processes_night_without_skipping_to_morning(completed):
    state = completed.state.village
    assert state.minute == 1380
    assert village_turns.next_actor(completed, stop_at_minute=1440) is None
    assert state.minute == 1440
    assert read_json(completed.path)["village"]["minute"] == 1440
    assert all(person.life.sleeping for person in state.residents.values())
    before = completed.state.model_dump(mode="json")
    state.free_turn_actor = next(iter(state.residents))
    assert village_turns.next_actor(completed, stop_at_minute=1440) is None
    state.free_turn_actor = before["village"]["free_turn_actor"]
    assert completed.state.model_dump(mode="json") == before
    assert village_turns.next_actor(completed) in state.residents
    assert state.minute > 1440


@pytest.mark.asyncio
@pytest.mark.parametrize("unlimited", [False, True])
async def test_goal_reached_before_midnight_does_not_end_monitoring(configuration, unlimited):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    controller.run = {
        "id": "run", "actor_id": "torvin", "session_id": "session", "scenario_id": "fixture",
        "cycles": 1, "goal": {"type": "village_restored"}, "config": config.model_dump(mode="json"),
        "village_stop_minute": 1440, "village_minute": 1380,
        "run_until_complete": unlimited,
    }
    controller.events = EventStore(data / "test-run", "run")
    controller.world = AsyncMock()
    controller.world.manifest = {}
    controller.world.operation.return_value = {
        "status": "achieved", "reason": "Everyone is home and asleep.", "observation_revision": "r1",
        "evidence": {"completion_scope": "full_village_day"},
    }
    assert await controller.verify() is False
    assert controller.run["verification"]["status"] == "achieved"
    assert controller.events.events[-1]["kind"] == "verification"


def test_world_inspection_caches_unchanged_files_without_leaking_or_aliasing_requests(configuration, cast, monkeypatch):
    import npc_sim.controller as module
    data, config = configuration
    world = cast["torvin"]
    path = data / "worlds" / world.state.world_id / "world-state.json"
    world.state.requests["large-result"] = {"text": "x" * 3_000_000}
    write_json(path, world.state.model_dump(mode="json"))
    controller = Controller(ROOT, data, config)
    controller.run = {"world_id": world.state.world_id}
    reads = []
    original = module.read_json

    def counted(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(module, "read_json", counted)
    first = controller.status()["world"]
    assert "requests" not in first and "admin_requests" not in first
    first["village"]["minute"] = 9999
    for _ in range(10):
        assert controller.status()["world"]["village"]["minute"] == world.state.village.minute
    assert reads == [path]
    world.state.village.minute += 5
    write_json(path, world.state.model_dump(mode="json"))
    assert controller.status()["world"]["village"]["minute"] == world.state.village.minute
    assert reads == [path, path]


@pytest.mark.asyncio
async def test_timeout_recovery_keeps_already_scheduled_next_actor(configuration, cast):
    data, config = configuration
    world = cast["torvin"]
    world.state.village.free_turn_actor = None
    world.state.village.last_scheduled_actor = "ada"
    write_json(data / "worlds" / world.state.world_id / "world-state.json", world.state.model_dump(mode="json"))
    controller = Controller(ROOT, data, config)
    controller.run = {
        "id": "run", "world_id": world.state.world_id, "status": "error",
        "reason": "Timed out while waiting for response to ClientRequest. Waited 15.0 seconds.",
        "actor_id": "elsie", "session_id": "session-elsie", "scenario_id": "millhaven-expanded",
        "goal": {"type": "village_restored", "status": "failed"}, "config": config.model_dump(mode="json"),
        "cycles": 1, "agent_profiles": {"elsie": {"name": "Elsie"}, "ada": {"name": "Ada"}},
    }
    controller.agents = {rid: {"memory": {}, "goal": {"status": "failed"}}
                         for rid in ["elsie", "ada"]}
    controller.events = EventStore(data / "test-run", "run")
    controller.events.events = [{
        "id": "run:1", "kind": "action_result", "actor_id": "elsie", "free_action": False,
        "payload": {"request_id": "confirmed"},
    }]
    controller.world = AsyncMock()
    controller.world.manifest = {"village_scheduler": "world_next_village_actor"}
    controller.world.operation.return_value = {
        "status": "active", "reason": "The day is unfinished.", "observation_revision": "r1",
        "evidence": {"completion_scope": "full_village_day"},
    }
    await controller.reopen(ReopenRequest(run_id="run", until_midnight=True))
    assert controller.run["status"] == "paused"
    assert controller.run["pending_actor_id"] == "ada"
    assert controller.run["village_stop_minute"] == 1440
