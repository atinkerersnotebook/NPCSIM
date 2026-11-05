import json

import httpx
import pytest

from npc_sim.controller import Controller
from npc_sim.dungeon_master import DungeonMasterConfig, DungeonMasterReview, load_config, validate_review
from npc_sim.assessment import Finding
from npc_sim.proxy import Proxy
from npc_sim.models import ProxyConfig
from npc_sim.storage import read_json, write_json
from test_controller import ROOT, TestProxy, configuration


def test_supervisor_configuration_and_evidence_authority(tmp_path):
    config = load_config(tmp_path)
    assert config.name == "Dungeon Master"
    assert read_json(tmp_path / "dungeon-master.json")["enabled"] is True
    config.name = "Oracle"
    write_json(tmp_path / "dungeon-master.json", config.model_dump(mode="json"))
    assert load_config(tmp_path).name == "Oracle"
    review = DungeonMasterReview(verdict="defeat", findings=[
        Finding(reason="Repeated confirmed failures.", evidence_ids=["event:1"])])
    validate_review(review, [{"id": "event:1"}], config)
    with pytest.raises(ValueError, match="outside"):
        validate_review(review, [{"id": "event:2"}], config)
    config.capabilities.remove("declare_defeat")
    with pytest.raises(ValueError, match="declare_defeat authority"):
        validate_review(review, [{"id": "event:1"}], config)
    with pytest.raises(ValueError, match="observe_team"):
        DungeonMasterConfig(capabilities=["review_progress"])


class FutilityProxy(TestProxy):
    async def review_progress(self, model, messages, correlation_id, timeout):
        payload = json.loads(messages[1]["content"])
        results = [event for event in payload["evidence"] if event["kind"] == "action_result"]
        if len(results) < 3:
            return await super().review_progress(model, messages, correlation_id, timeout)
        return DungeonMasterReview(verdict="defeat", findings=[
            Finding(reason="The team repeatedly attempted the same action without changing its situation.",
                    evidence_ids=[event["id"] for event in results])]), {}


@pytest.mark.asyncio
async def test_ai_judgment_stops_repeated_futility_without_leaking_privileged_context(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    proxy = FutilityProxy(controller.proxy, [("look", {})] * 4)
    controller.proxy = proxy
    await controller.start_worker()
    try:
        await controller.command("start")
        for _ in range(3):
            await controller.command("step")
        assert controller.run["status"] == "blocked"
        assert "Dungeon Master" in controller.run["reason"]
        assert controller.run["actions"] == controller.run["model_calls"] == 3
        assert controller.run["dungeon_master"]["model_calls"] == 3
        assert controller.run["successful"] is False
        rulings = [event["payload"] for event in controller.events.events
                   if event["kind"] == "dungeon_master_ruling"]
        assert any(ruling["verdict"] == "allowed" and ruling["source"] == "world" for ruling in rulings)
        assert rulings[-1]["verdict"] == "defeat" and rulings[-1]["source"] == "ai"
        assert all("dungeon_master" not in context["memory"] for context in proxy.contexts)
        assert all("team" not in context["memory"] for context in proxy.contexts)
        assert read_json(controller.events.directory / "run.json")["dungeon_master"]["model_calls"] == 3
        with pytest.raises(ValueError, match="Start a new episode"):
            await controller.command("step")
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_supervisor_reviews_once_per_team_round_and_configuration_is_frozen(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    proxy = TestProxy(controller.proxy, [("look", {})] * 3)
    controller.proxy = proxy
    await controller.start_worker()
    try:
        await controller.command("start", {"scenario_id": "storm-signal"})
        edited = load_config(data)
        edited.name = "Overseer"
        write_json(data / "dungeon-master.json", edited.model_dump(mode="json"))
        for _ in range(2):
            await controller.command("step")
            assert controller.run["dungeon_master"]["model_calls"] == 0
        await controller.command("step")
        assert controller.run["dungeon_master"]["model_calls"] == 1
        context = proxy.dungeon_master_contexts[0]
        assert set(context["team"]) == {"raven", "badger", "otter"}
        assert len({agent["observation"]["self"]["location_id"] for agent in context["team"].values()}) == 3
        assert context["config"]["name"] == "Dungeon Master"
        for context in proxy.contexts:
            assert "dungeon-master" not in json.dumps(context)
        await controller.command("reset")
        assert controller.run["dungeon_master"]["config"]["name"] == "Overseer"
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_world_defeat_needs_no_ai_call_and_disabled_supervisor_never_grants_skills(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    controller.proxy = TestProxy(controller.proxy, [])
    await controller.start_worker()
    try:
        await controller.command("start", {"scenario_id": "sealed-vault", "skill_mode": "npc_only",
                                           "agent_assignments": {"thief": "fox", "mage": "mage"}})
        await controller.command("step")
        assert controller.run["status"] == "blocked"
        assert controller.run["dungeon_master"]["model_calls"] == 0
        assert any(event["kind"] == "dungeon_master_ruling" and event["payload"]["source"] == "world"
                   and event["payload"]["verdict"] == "defeat" for event in controller.events.events)
        write_json(data / "dungeon-master.json", DungeonMasterConfig(enabled=False).model_dump(mode="json"))
        controller.proxy = TestProxy(controller.proxy.real_proxy, [("open", {"target": "door"})])
        await controller.command("start", {"scenario_id": "escape-room"})
        await controller.command("step")
        assert controller.run["dungeon_master"]["model_calls"] == 0
        assert not controller.proxy.dungeon_master_contexts
        assert not any(event["kind"] == "dungeon_master_ruling" for event in controller.events.events)
        assert controller.run["status"] == "paused"
        assert any(event["kind"] == "action_result" and event["payload"]["outcome"] == "REJECTED"
                   for event in controller.events.events)
        assert controller.status()["world"]["entities"]["door"]["locked"] is True
    finally:
        await controller.shutdown()


class InvalidEvidenceProxy(TestProxy):
    async def review_progress(self, model, messages, correlation_id, timeout):
        return DungeonMasterReview(verdict="defeat", findings=[
            Finding(reason="Invented evidence is not admissible.", evidence_ids=["invented:event"])]), {}


@pytest.mark.asyncio
async def test_invalid_supervisor_ruling_gets_one_repair_then_explicit_error(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    controller.proxy = InvalidEvidenceProxy(controller.proxy, [("look", {})])
    await controller.start_worker()
    try:
        await controller.command("start")
        await controller.command("step")
        assert controller.run["status"] == "error"
        assert controller.run["actions"] == controller.run["model_calls"] == 1
        assert controller.run["dungeon_master"]["model_calls"] == 2
        assert "Dungeon Master ruling remained invalid" in controller.run["reason"]
        assert len([event for event in controller.events.events
                    if event["kind"] == "dungeon_master_validation"]) == 2
        assert not any(event["kind"] == "dungeon_master_ruling" and event["payload"]["source"] == "ai"
                       for event in controller.events.events)
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_progress_reviews_use_selected_local_proxy_with_structured_evidence(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert "untrusted data" in body["messages"][0]["content"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "verdict": "continue", "findings": [{"reason": "Useful discovery.", "evidence_ids": ["event:1"]}]
        })}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(handler)))
    proxy = Proxy(ProxyConfig())
    messages = proxy.dungeon_master_context({"evidence": [{"id": "event:1"}]})
    review, _ = await proxy.review_progress("test-model", messages, "dm:test", 5)
    assert review.verdict == "continue"
    assert seen[0].url.host == "127.0.0.1" and seen[0].url.port == 9901
    assert seen[0].headers["x-request-id"] == "dm:test"
