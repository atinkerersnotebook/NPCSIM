import copy

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import npc_sim.app as application
from npc_sim.controller import Controller
from npc_sim.episode_saves import list_saves, read_save
from npc_sim.interactions import NpcReply
from npc_sim.models import EpisodeLoadRequest, EpisodeSaveRequest
from npc_sim.storage import read_json, write_json
from test_controller import ROOT, SOLUTION, TestProxy, configuration


class SaveProxy(TestProxy):
    def __init__(self, proxy, actions):
        super().__init__(proxy, actions)
        self.questions = []

    def question_context(self, goal, observed, memory, question):
        self.questions.append(copy.deepcopy((goal, observed, memory, question)))
        return self.real_proxy.question_context(goal, observed, memory, question)

    async def answer_question(self, *args):
        return NpcReply(answer="I only remember what I observed before this save point."), {}


@pytest.mark.parametrize("body", [
    {}, {"run_id": "../outside", "name": "test"},
    {"run_id": "a" * 32, "name": " "}, {"run_id": "a" * 32, "name": "x" * 121},
    {"run_id": "a" * 32, "name": "test", "extra": True},
])
def test_invalid_save_requests_are_rejected(body):
    with pytest.raises(ValidationError):
        EpisodeSaveRequest.model_validate(body)


@pytest.mark.parametrize("body", [
    {}, {"save_id": "../outside"}, {"save_id": "a" * 32, "current_run_id": "old"},
])
def test_invalid_load_requests_are_rejected(body):
    with pytest.raises(ValidationError):
        EpisodeLoadRequest.model_validate(body)


@pytest.mark.asyncio
async def test_save_restores_exact_point_as_independent_playable_copy(configuration):
    data, config = configuration
    config.world.scenario = "escape-room"
    controller = Controller(ROOT, data, config)
    proxy = SaveProxy(controller.proxy, [SOLUTION[0], SOLUTION[1], SOLUTION[1]])
    controller.proxy = proxy
    await controller.start_worker()
    try:
        await controller.command("start")
        await controller.command("step")
        original_id, original_world = controller.run["id"], controller.run["world_id"]
        result = await controller.command("save_episode", {"run_id": original_id, "name": "Before opening box"})
        saved_id = result["save"]["id"]
        assert result["state"]["run"]["status"] == "paused"
        assert result["save"]["resumable"]
        assert len(proxy.contexts) == 1 and not proxy.completion_contexts
        frozen_bytes = (data / "saves" / f"{saved_id}.json").read_bytes()
        assert list_saves(data)[0]["name"] == "Before opening box"
        await controller.command("step")
        assert controller.status()["world"]["entities"]["box"]["open"] is True
        await controller.command("load_episode", {"save_id": saved_id, "current_run_id": original_id})
        loaded_id = controller.run["id"]
        assert loaded_id != original_id and controller.run["world_id"] != original_world
        assert controller.run["status"] == "paused"
        assert controller.run["actions"] == controller.run["model_calls"] == 1
        assert controller.status()["world"]["entities"]["box"]["open"] is False
        assert read_json(data / "runs" / original_id / "run.json")["actions"] == 2
        assert len(proxy.contexts) == 2 and not proxy.completion_contexts
        assert all(event["id"] == f"{loaded_id}:{event['sequence']}" for event in controller.events.events)
        assert controller.run["goal"]["id"] == f"{loaded_id}-goal"
        await controller.command("npc_chat", {
            "run_id": loaded_id, "actor_id": controller.run["actor_id"], "question": "What did you observe?"
        })
        assert controller.run["followup_model_calls"] == 1
        assert controller.run["model_calls"] == 1 and controller.run["actions"] == 1
        assert controller.status()["world"]["entities"]["box"]["open"] is False
        assert "key" not in {item["id"] for item in proxy.questions[-1][1]["visible_entities"]}
        await controller.command("step")
        assert controller.status()["world"]["entities"]["box"]["open"] is True
        assert controller.run["actions"] == controller.run["model_calls"] == 2
        assert not controller.run.get("snapshot_questions")
        assert (data / "saves" / f"{saved_id}.json").read_bytes() == frozen_bytes
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_named_save_and_branch_preserve_archived_retry_results(configuration, monkeypatch):
    from npc_sim.mcp_client import WorldClient
    from npc_sim.world import World
    from npc_sim.world_storage import read_world_state

    data, config = configuration
    config.world.scenario = "escape-room"
    controller = Controller(ROOT, data, config)
    controller.proxy = SaveProxy(controller.proxy, SOLUTION[:3])
    await controller.start_worker()
    try:
        await controller.command("start")
        for _ in range(3):
            await controller.command("step")
        state_path = data / "worlds" / controller.run["world_id"] / "world-state.json"
        assert read_world_state(state_path).request_history_id is not None
        result = await controller.command("save_episode", {
            "run_id": controller.run["id"], "name": "With archived retry history"})
        saved = read_save(data, result["save"]["id"])
        assert saved.world.request_history_id is None
        assert len(saved.world.requests) == 3
        original_connect = WorldClient.connect

        async def connect_prepared_world(client):
            prepared = read_world_state(client.state_path)
            assert prepared.request_history_id is not None
            assert len(prepared.requests) == 1
            await original_connect(client)

        monkeypatch.setattr(WorldClient, "connect", connect_prepared_world)
        await controller.command("load_episode", {
            "save_id": saved.id, "current_run_id": controller.run["id"]})
        run = controller.run
        state_path = data / "worlds" / run["world_id"] / "world-state.json"
        portable = read_world_state(state_path, include_history=True)
        assert len(portable.requests) == 3
        assert portable.request_history_id is None
        identity, entry = next(iter(portable.requests.items()))
        session_id, request_id = identity.split(":", 1)
        assert session_id == run["session_id"]
        world = World(saved.scenario, state_path, run["world_id"], run["actor_id"], session_id)
        before = world.snapshot()
        request = entry["request"]
        assert world.act(request_id, request["expected_observation_revision"],
                         request["action_id"], request["arguments"]) == entry["result"]
        assert world.snapshot() == before
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_saved_paused_episode_reconnects_after_restart_without_review_or_replay(configuration):
    data, config = configuration
    config.world.scenario = "escape-room"
    first = Controller(ROOT, data, config)
    first.proxy = SaveProxy(first.proxy, SOLUTION[:1])
    await first.start_worker()
    await first.command("start")
    await first.command("step")
    saved = await first.command("save_episode", {"run_id": first.run["id"], "name": "Restart test"})
    original_id = first.run["id"]
    await first.shutdown()
    restored = Controller(ROOT, data, config)
    proxy = SaveProxy(restored.proxy, SOLUTION[1:2])
    restored.proxy = proxy
    await restored.start_worker()
    try:
        assert restored.run["id"] == original_id
        assert restored.run["status"] == "paused" and restored.world is not None
        assert not proxy.contexts and not proxy.completion_contexts
        await restored.command("load_episode", {
            "save_id": saved["save"]["id"], "current_run_id": original_id})
        await restored.command("step")
        assert restored.run["actions"] == 2
        assert restored.status()["world"]["entities"]["box"]["open"] is True
    finally:
        await restored.shutdown()


@pytest.mark.asyncio
async def test_multi_npc_saves_preserve_turns_private_memories_and_frozen_profiles(configuration):
    data, config = configuration
    config.world.scenario = "storm-signal"
    controller = Controller(ROOT, data, config)
    proxy = SaveProxy(controller.proxy, [
        ("release_lens", {"target": "optical-cache"}), ("take", {"target": "mast-weight"})])
    controller.proxy = proxy
    await controller.start_worker()
    try:
        await controller.command("start")
        await controller.command("step")
        source = controller.run["id"]
        before = copy.deepcopy(controller.agents)
        profiles = copy.deepcopy(controller.run["agent_profiles"])
        saved = await controller.command("save_episode", {"run_id": source, "name": "Raven's first turn"})
        await controller.command("load_episode", {"save_id": saved["save"]["id"], "current_run_id": source})
        assert controller.run["next_agent_index"] == 1
        assert controller.run["agent_profiles"] == profiles
        sessions = set()
        for actor_id, agent in controller.agents.items():
            assert agent["session_id"] != before[actor_id]["session_id"]
            assert agent["memory"]["recent"] == before[actor_id]["memory"]["recent"]
            sessions.add(agent["session_id"])
        assert len(sessions) == len(controller.agents)
        run_id = controller.run["id"]
        await controller.command("npc_chat", {"run_id": run_id, "actor_id": "otter", "question": "What do you know?"})
        assert "operator_conversation" in controller.agents["otter"]["memory"]
        assert "operator_conversation" not in controller.agents["raven"]["memory"]
        assert controller.run["actor_id"] == "raven"
        await controller.command("step")
        assert controller.run["actor_id"] == "badger"
        assert controller.run["actions"] == 2
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_completed_episode_loads_for_questions_without_restarting_or_regrading(configuration):
    data, config = configuration
    config.world.scenario = "escape-room"
    controller = Controller(ROOT, data, config)
    proxy = SaveProxy(controller.proxy, SOLUTION)
    controller.proxy = proxy
    await controller.start_worker()
    try:
        await controller.command("start")
        for _ in SOLUTION:
            await controller.command("step")
        assert controller.run["status"] == "succeeded"
        review = copy.deepcopy(controller.run["completion_analysis"])
        source = controller.run["id"]
        saved = await controller.command("save_episode", {"run_id": source, "name": "Finished adventure"})
        assert not saved["save"]["resumable"]
        calls = len(proxy.completion_contexts)
        await controller.command("load_episode", {"save_id": saved["save"]["id"], "current_run_id": source})
        assert controller.run["status"] == "succeeded"
        assert controller.run["completion_analysis"]["verdict"] == review["verdict"]
        assert len(proxy.completion_contexts) == calls
        with pytest.raises(ValueError, match="Start a new"):
            await controller.command("resume")
        await controller.command("npc_chat", {
            "run_id": controller.run["id"], "actor_id": controller.run["actor_id"], "question": "How did it end?"})
        assert controller.run["status"] == "succeeded"
        assert controller.run["actions"] == len(SOLUTION)
        assert controller.run["followup_model_calls"] == 1
        assert len(proxy.completion_contexts) == calls
        # Completion evidence must refer to the copied transcript, not the original.
        for paragraph in controller.run["completion_analysis"]["story"]:
            assert paragraph["id"].startswith(controller.run["id"] + ":")
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_missing_corrupt_and_stale_saves_leave_current_episode_unchanged(configuration):
    data, config = configuration
    config.world.scenario = "escape-room"
    controller = Controller(ROOT, data, config)
    controller.proxy = SaveProxy(controller.proxy, [])
    await controller.start_worker()
    try:
        await controller.command("start")
        saved = await controller.command("save_episode", {"run_id": controller.run["id"], "name": "Good save"})
        before = copy.deepcopy(controller.status())
        with pytest.raises(ValueError, match="not found"):
            await controller.command("load_episode", {"save_id": "0" * 32, "current_run_id": controller.run["id"]})
        with pytest.raises(ValueError, match="changed"):
            await controller.command("load_episode", {"save_id": saved["save"]["id"], "current_run_id": "0" * 32})
        path = data / "saves" / f"{saved['save']['id']}.json"
        payload = read_json(path)
        payload["world"]["world_id"] = "different-world"
        write_json(path, payload)
        with pytest.raises(ValueError, match="do not match"):
            await controller.command("load_episode", {"save_id": saved["save"]["id"], "current_run_id": controller.run["id"]})
        assert controller.status() == before
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_load_connection_failure_rolls_back_without_changing_save_or_original_world(configuration, monkeypatch):
    from npc_sim.mcp_client import WorldClient

    data, config = configuration
    config.world.scenario = "escape-room"
    controller = Controller(ROOT, data, config)
    controller.proxy = SaveProxy(controller.proxy, [])
    await controller.start_worker()
    try:
        await controller.command("start")
        source, world_id = controller.run["id"], controller.run["world_id"]
        saved = await controller.command("save_episode", {"run_id": source, "name": "Rollback"})
        original_connect = WorldClient.connect

        async def fail_copy(client):
            if client.world_id != world_id:
                raise ValueError("Copy connection failed")
            await original_connect(client)

        monkeypatch.setattr(WorldClient, "connect", fail_copy)
        with pytest.raises(ValueError, match="Copy connection failed"):
            await controller.command("load_episode", {"save_id": saved["save"]["id"], "current_run_id": source})
        assert controller.run["id"] == source and controller.run["status"] == "paused"
        assert controller.world.world_id == world_id and controller.world.session is not None
        assert [path.name for path in (data / "runs").iterdir()] == [source]
        assert read_save(data, saved["save"]["id"]).run["id"] == source
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_living_village_save_preserves_systems_clock_budgets_and_midnight_boundary(configuration):
    from npc_sim.living_village import build_scenario
    from npc_sim.models import Scenario

    data, config = configuration
    value = build_scenario(Scenario.model_validate(read_json(data / "scenarios" / "millhaven-expanded.json")))
    value.village.start_minute = 480
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
    write_json(data / "scenarios" / f"{value.id}.json", value.model_dump(mode="json"))
    config.world.scenario = value.id
    controller = Controller(ROOT, data, config)
    controller.proxy = SaveProxy(controller.proxy, [])
    await controller.start_worker()
    try:
        await controller.command("start")
        source = controller.run["id"]
        await controller.command("run_mode", {"run_id": source, "run_until_complete": True})
        saved = await controller.command("save_episode", {"run_id": source, "name": "Living village morning"})
        snapshot = read_save(data, saved["save"]["id"])
        budgets = copy.deepcopy(controller.run["config"]["budgets"])
        await controller.command("load_episode", {"save_id": saved["save"]["id"], "current_run_id": source})
        restored = read_json(controller.world.state_path)["village"]
        original = snapshot.world.village.model_dump(mode="json")
        assert restored["minute"] == original["minute"] == 480
        for field in ("economy", "social", "incidents", "wagon", "stocks", "coins", "stock_lots",
                      "residents", "time_use"):
            assert restored[field] == original[field]
        assert controller.run["run_until_complete"]
        assert controller.run["village_stop_minute"] == 1440
        assert controller.run["config"]["budgets"] == budgets
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_unconfirmed_action_save_is_questions_only(configuration):
    data, config = configuration
    config.world.scenario = "escape-room"
    controller = Controller(ROOT, data, config)
    controller.proxy = SaveProxy(controller.proxy, [])
    await controller.start_worker()
    try:
        await controller.command("start")
        source = controller.run["id"]
        controller.emit("action_request", {"request_id": "uncertain-action"})
        saved = await controller.command("save_episode", {"run_id": source, "name": "Uncertain boundary"})
        assert not saved["save"]["resumable"]
        await controller.command("load_episode", {"save_id": saved["save"]["id"], "current_run_id": source})
        assert controller.run["status"] == "error"
        assert controller.run["completion_analysis"]["status"] == "error"
        await controller.command("npc_chat", {
            "run_id": controller.run["id"], "actor_id": controller.run["actor_id"], "question": "What did you see?"
        })
        assert controller.run["followup_model_calls"] == 1
        assert controller.run["actions"] == 0
    finally:
        await controller.shutdown()


def test_episode_save_api_validates_without_model_calls(configuration, monkeypatch):
    data, config = configuration
    config.world.scenario = "escape-room"
    write_json(data / "config.json", config.model_dump(mode="json"))
    monkeypatch.setattr(application, "DATA", data)
    with TestClient(application.app) as client:
        initial = client.post("/api/control/start", json={}).json()
        run_id = initial["run"]["id"]
        assert client.post("/api/saves", json={"run_id": run_id, "name": " "}).status_code == 422
        response = client.post("/api/saves", json={"run_id": run_id, "name": "API save"})
        assert response.status_code == 200
        saved = response.json()["save"]
        assert client.get("/api/saves").json()["saves"][0] == saved
        assert client.post("/api/saves/load", json={"save_id": "../outside"}).status_code == 422
        response = client.post("/api/saves/load", json={"save_id": saved["id"], "current_run_id": run_id})
        assert response.status_code == 200
        loaded = response.json()["run"]
        assert loaded["id"] != run_id and loaded["status"] == "paused"
        assert loaded["actions"] == loaded["model_calls"] == 0
