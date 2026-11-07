import shutil
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

import npc_sim.app as application
from npc_sim.storage import read_json
from npc_sim.proxy import Proxy
from test_controller import assessment_response


ROOT = Path(__file__).resolve().parent.parent


def test_live_event_projection_keeps_original_details_available(monkeypatch):
    from types import SimpleNamespace
    original = {"id": "run:1", "sequence": 1, "kind": "proxy_request",
                "payload": {"model": "test", "messages": [{"content": "x" * 1_000_000}]}}
    active = SimpleNamespace(run={"id": "run"}, events=SimpleNamespace(events=[original]))
    monkeypatch.setattr(application.app.state, "controller", active, raising=False)
    client = TestClient(application.app)
    compact = client.get("/api/events?compact=true")
    assert len(compact.content) < 1000
    assert compact.json()["events"][0]["payload"]["technical_payload_omitted"] is True
    assert "messages" not in compact.json()["events"][0]["payload"]
    assert client.get("/api/events/1?run_id=run").json() == original
    assert client.get("/api/events").json()["events"] == [original]
    assert client.get("/api/events/1?run_id=old").status_code == 409
    assert client.get("/api/events/2?run_id=run").status_code == 404
    assert client.get("/api/events?compact=true&after=1").json()["events"] == []


@pytest.mark.asyncio
async def test_stream_cursor_skips_loaded_history_and_unchanged_snapshots(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    recorded = [{"id": f"run:{i}", "sequence": i, "kind": "proxy_request",
                 "payload": {"messages": ["private context"]}} for i in range(1, 4)]
    active = SimpleNamespace(run={"id": "run"}, events=SimpleNamespace(events=recorded),
                             status=lambda: {"run": {"id": "run"}})
    monkeypatch.setattr(application, "controller", lambda request: active)
    request = SimpleNamespace(headers={}, is_disconnected=AsyncMock(return_value=False))
    response = await application.stream(request, run_id="run", after=2, compact=True)
    iterator = response.body_iterator
    try:
        assert "event: snapshot" in await anext(iterator)
        next_event = await anext(iterator)
        assert "id: run:3" in next_event and "private context" not in next_event
        assert "heartbeat" in await anext(iterator)
        assert "heartbeat" in await anext(iterator)
    finally:
        await iterator.aclose()


@pytest.fixture(autouse=True)
def local_assessment_fixture(monkeypatch):
    async def assess(self, model, messages, correlation_id, timeout):
        return assessment_response(messages), {}
    monkeypatch.setattr(Proxy, "assess", assess)


def test_admin_controls_inspection_and_actor_need_isolation(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data" / "scenarios", data / "scenarios")
    shutil.copy(ROOT / "data" / "config.json", data / "config.json")
    monkeypatch.setattr(application, "DATA", data)
    with TestClient(application.app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        choices = client.get("/api/scenarios").json()["scenarios"]
        assert {"escape-room", "two-locked-rooms"} <= {choice["id"] for choice in choices}
        assert client.get("/api/state").json()["run"] is None
        initial = client.post("/api/control/start", json={}).json()
        assert initial["run"]["status"] == "ready"
        assert initial["world"]["entities"]["key"]["location"] == "box"
        assert "key" not in [e["id"] for e in initial["observation"]["visible_entities"]]
        actor_id = initial["run"]["actor_id"]
        toggled = client.put(f"/api/actors/{actor_id}/needs/sleep", json={"enabled": True}).json()
        assert toggled["world"]["actors"][actor_id]["needs"]["sleep"]["enabled"]
        assert toggled["observation"]["self"]["needs"][1]["enabled"]
        assert "rest" in [action["id"] for action in toggled["observation"]["actions"]]
        assert client.put("/api/actors/unknown/needs/sleep", json={"enabled": True}).status_code == 409
        assert client.get("/api/state").json()["run"]["status"] == "ready"
        assert client.get("/api/events").json()["events"][-1]["kind"] == "observation"
        current = client.get("/api/events").json()
        assert client.get("/api/events", params={"after": 2}).json()["events"] == current["events"][2:]
        assert client.get("/api/transcript").json()["events"] == current["events"]
        reset = client.post("/api/control/reset", json={}).json()
        assert reset["run"]["id"] != initial["run"]["id"]
        assert len(client.get("/api/history").json()["runs"]) == 2
        archived = client.get(f"/api/history/{initial['run']['id']}").json()
        assert archived["run"]["status"] == "cancelled"
        state_path = data / "worlds" / initial["run"]["world_id"] / "world-state.json"
        assert read_json(state_path)["actors"][actor_id]["needs"]["sleep"]["enabled"]


@pytest.mark.parametrize("scenario_id", ["rock-paper-scissors", "tic-tac-toe"])
def test_games_are_selectable_and_start_two_players_without_premature_success(tmp_path, monkeypatch, scenario_id):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data" / "scenarios", data / "scenarios")
    shutil.copy(ROOT / "data" / "config.json", data / "config.json")
    monkeypatch.setattr(application, "DATA", data)
    with TestClient(application.app) as client:
        choices = client.get("/api/scenarios").json()["scenarios"]
        assert any(choice["id"] == scenario_id for choice in choices)
        response = client.post("/api/control/start", json={"scenario_id": scenario_id})
        assert response.status_code == 200
        initial = response.json()
        assert initial["run"]["status"] == "ready"
        assert initial["run"]["goal"]["status"] == "active"
        assert set(initial["run"]["agents"]) == {"nova", "atlas"}
        if scenario_id == "rock-paper-scissors":
            assert initial["world"]["rps"] == {"pending": {}, "history": []}
        else:
            assert initial["world"]["tic_tac_toe"] == {"history": []}
        for agent in initial["run"]["agents"].values():
            if scenario_id == "rock-paper-scissors":
                assert agent["observation"]["rps"]["rounds_total"] == 5
                action = "play_rps"
            else:
                assert agent["observation"]["tic_tac_toe"]["board"] == [None] * 9
                action = "play_tic_tac_toe"
            assert action in {action["id"] for action in agent["observation"]["actions"]}


def test_moonwake_scenario_is_selectable_and_keeps_default_unchanged(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data" / "scenarios", data / "scenarios")
    shutil.copy(ROOT / "data" / "config.json", data / "config.json")
    monkeypatch.setattr(application, "DATA", data)
    with TestClient(application.app) as client:
        choices = client.get("/api/scenarios").json()["scenarios"]
        assert any(choice["id"] == "moonwake-treasures" for choice in choices)
        response = client.post("/api/control/start", json={"scenario_id": "moonwake-treasures"})
        assert response.status_code == 200
        initial = response.json()
        assert initial["run"]["scenario_id"] == "moonwake-treasures"
        assert initial["run"]["goal"]["type"] == "treasures_returned"
        assert initial["world"]["voyage"]["turns"] == 0
        assert initial["config"]["world"]["scenario"] == "escape-room"
        assert initial["run"]["config"]["budgets"]["max_actions"] == 400
        reset = client.post("/api/control/reset", json={}).json()
        assert reset["run"]["scenario_id"] == "moonwake-treasures"
        assert reset["world"]["voyage"]["turns"] == 0


def test_five_asymmetric_scenarios_are_selectable_with_distinct_bodies(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data" / "scenarios", data / "scenarios")
    shutil.copy(ROOT / "data" / "config.json", data / "config.json")
    monkeypatch.setattr(application, "DATA", data)
    expected = {"river-crossing", "sealed-vault", "fallen-observatory", "flooded-lighthouse", "divided-temple"}
    with TestClient(application.app) as client:
        choices = client.get("/api/scenarios")
        assert choices.status_code == 200
        assert expected <= {choice["id"] for choice in choices.json()["scenarios"]}
        for scenario_id in sorted(expected):
            response = client.post("/api/control/start", json={"scenario_id": scenario_id})
            assert response.status_code == 200
            state = response.json()
            assert state["run"]["scenario_id"] == scenario_id
            assert state["config"]["world"]["scenario"] == "escape-room"
            actors = list(state["world"]["actors"].values())
            assert len(actors) == 2
            assert actors[0]["body"]["capabilities"] != actors[1]["body"]["capabilities"]
            assert state["world"]["skill_uses"] == {}
            assert state["observation"]["skill_challenges"]
            assert len(state["run"]["agents"]) == 2


def test_admin_origin_and_path_guards(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(ROOT / "data" / "config.json", data / "config.json")
    monkeypatch.setattr(application, "DATA", data)
    with TestClient(application.app) as client:
        assert client.post("/api/control/start", json={}, headers={"Origin": "https://untrusted.example"}).status_code == 403
        assert client.get("/api/state", headers={"Host": "untrusted.example"}).status_code == 403
        assert client.get("/api/history/not-a-run").status_code == 400
        assert client.post("/api/control/invented", json={}).status_code == 404
        assert client.post("/api/control/start", json={"scenario_id": "../secret"}).status_code == 422


def test_agent_catalog_edit_assignment_and_frozen_episode(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data" / "scenarios", data / "scenarios")
    shutil.copy(ROOT / "data" / "config.json", data / "config.json")
    monkeypatch.setattr(application, "DATA", data)
    with TestClient(application.app) as client:
        catalog = client.get("/api/agents").json()["agents"]
        raven = next(profile for profile in catalog if profile["id"] == "raven")
        choices = client.get("/api/scenarios").json()["scenarios"]
        storm = next(choice for choice in choices if choice["id"] == "storm-signal")
        assert {slot["profile_id"] for slot in storm["agent_slots"]} == {"raven", "badger", "otter"}
        raven["personality"] = "A patient and skeptical scout."
        assert client.put("/api/agents/raven", json=raven).status_code == 200
        assert read_json(data / "agents" / "raven.json")["personality"] == raven["personality"]
        response = client.post("/api/control/start", json={
            "scenario_id": "escape-room", "agent_assignments": {"npc-001": "raven"}})
        assert response.status_code == 200
        initial = response.json()
        assert "fly" in initial["world"]["actors"]["npc-001"]["body"]["capabilities"]
        assert initial["run"]["agent_profiles"]["npc-001"]["personality"] == raven["personality"]
        raven["personality"] = "A different persona."
        assert client.put("/api/agents/raven", json=raven).status_code == 200
        assert client.get("/api/state").json()["run"]["agent_profiles"]["npc-001"]["personality"] != raven["personality"]
        reset = client.post("/api/control/reset", json={}).json()
        assert reset["run"]["agent_assignments"] == {"npc-001": "raven"}
        assert reset["run"]["agent_profiles"]["npc-001"]["personality"] == raven["personality"]
        assert client.put("/api/agents/badger", json=raven).status_code == 422
        assert client.put("/api/agents/Raven", json={**raven, "id": "Raven"}).status_code == 409
        invalid = {**raven, "id": "../outside"}
        assert client.put("/api/agents/raven", json=invalid).status_code == 422
        invalid = {**raven, "body": {**raven["body"], "capacity": -1}}
        assert client.put("/api/agents/raven", json=invalid).status_code == 422
        invalid = client.post("/api/control/start", json={
            "scenario_id": "storm-signal",
            "agent_assignments": {"raven": "nova", "badger": "badger", "otter": "otter"}})
        assert invalid.status_code == 409
        assert "incompatible" in invalid.json()["detail"]
        assert client.get("/api/state").json()["run"]["id"] == reset["run"]["id"]


def test_agent_markdown_api_persists_partial_updates_and_freezes_new_adventures(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data" / "scenarios", data / "scenarios")
    shutil.copy(ROOT / "data" / "config.json", data / "config.json")
    monkeypatch.setattr(application, "DATA", data)
    with TestClient(application.app) as client:
        raven = next(profile for profile in client.get("/api/agents").json()["agents"] if profile["id"] == "raven")
        first = "# Soul\n\nPatient, curious and careful.\n"
        response = client.put("/api/agents/raven", json={**raven, "documents": {"SOUL.md": first}})
        assert response.status_code == 200
        assert response.json()["documents"]["SOUL.md"] == first
        assert (data / "agents" / "raven" / "SOUL.md").read_text(encoding="utf-8") == first
        assert "documents" not in read_json(data / "agents" / "raven.json")
        initial = client.post("/api/control/start", json={
            "scenario_id": "escape-room", "agent_assignments": {"npc-001": "raven"}}).json()
        assert initial["run"]["agent_profiles"]["npc-001"]["documents"]["SOUL.md"] == first
        second = "# Soul\n\nA quieter and more cautious temperament.\n"
        assert client.put("/api/agents/raven", json={**raven, "documents": {"SOUL.md": second}}).status_code == 200
        frozen = client.get("/api/state").json()["run"]["agent_profiles"]["npc-001"]
        assert frozen["documents"]["SOUL.md"] == first
        reset = client.post("/api/control/reset", json={}).json()
        assert reset["run"]["agent_profiles"]["npc-001"]["documents"]["SOUL.md"] == second
        assert reset["world"]["actors"]["npc-001"]["body"]["capabilities"] == raven["body"]["capabilities"]
        for name in ("../outside.md", "people/../../outside.md", "people\\outside.md"):
            assert client.put("/api/agents/raven", json={**raven, "documents": {name: "Invalid"}}).status_code == 422
        conflict = client.put("/api/agents/raven", json={
            **raven, "personality": "An incompatible legacy edit.", "documents": {}})
        assert conflict.status_code == 422
        assert "customized" in conflict.json()["detail"]


def test_select_and_reset_two_room_scenario_without_changing_default(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data" / "scenarios", data / "scenarios")
    shutil.copy(ROOT / "data" / "config.json", data / "config.json")
    monkeypatch.setattr(application, "DATA", data)
    with TestClient(application.app) as client:
        initial = client.post("/api/control/start", json={"scenario_id": "two-locked-rooms"}).json()
        assert initial["run"]["scenario_id"] == "two-locked-rooms"
        assert initial["world"]["exit_location"] == "outside"
        assert initial["run"]["goal"]["type"] == "actor_at_exit_location"
        assert initial["config"]["world"]["scenario"] == "escape-room"
        configured = client.put("/api/config", json=initial["config"]).json()
        assert configured["run"]["config"]["world"]["scenario"] == "two-locked-rooms"
        assert client.post("/api/control/start", json={"scenario_id": "unknown"}).status_code == 409
        assert client.get("/api/state").json()["run"]["id"] == initial["run"]["id"]
        reset = client.post("/api/control/reset", json={}).json()
        assert reset["run"]["scenario_id"] == "two-locked-rooms"
        assert reset["world"]["entities"]["box"]["name"] == "Box"
        assert reset["world"]["entities"]["box"]["health"] == 50


def test_npc_only_policy_starts_intractable_team_and_validates_mode(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data" / "scenarios", data / "scenarios")
    shutil.copy(ROOT / "data" / "config.json", data / "config.json")
    monkeypatch.setattr(application, "DATA", data)
    with TestClient(application.app) as client:
        payload = {"scenario_id": "sealed-vault", "skill_mode": "npc_only",
                   "agent_assignments": {"thief": "raven", "mage": "mage"}}
        response = client.post("/api/control/start", json=payload)
        assert response.status_code == 200
        state = response.json()
        assert state["run"]["skill_mode"] == "npc_only"
        assert state["run"]["status"] == "ready"
        capabilities = state["world"]["actors"]["thief"]["body"]["capabilities"]
        assert "fly" in capabilities and "lockpicking" not in capabilities
        challenge = next(challenge for challenge in state["observation"]["skill_challenges"]
                         if challenge["id"] == "pick_gallery_lock")
        assert "Requires lockpicking" in challenge["reason"]
        reset = client.post("/api/control/reset", json={})
        assert reset.status_code == 200
        assert reset.json()["run"]["skill_mode"] == "npc_only"
        assert client.post("/api/control/start", json={**payload, "skill_mode": "invented"}).status_code == 422


def test_dungeon_master_configuration_is_editable_and_frozen_per_episode(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data" / "scenarios", data / "scenarios")
    shutil.copy(ROOT / "data" / "config.json", data / "config.json")
    monkeypatch.setattr(application, "DATA", data)
    with TestClient(application.app) as client:
        config = client.get("/api/dungeon-master").json()
        assert config["name"] == "Dungeon Master"
        initial = client.post("/api/control/start", json={}).json()
        assert initial["run"]["dungeon_master"]["config"]["enabled"] is True
        config["name"] = "Oracle"
        assert client.put("/api/dungeon-master", json=config).status_code == 200
        assert read_json(data / "dungeon-master.json")["name"] == "Oracle"
        assert client.get("/api/state").json()["run"]["dungeon_master"]["config"]["name"] == "Dungeon Master"
        reset = client.post("/api/control/reset", json={}).json()
        assert reset["run"]["dungeon_master"]["config"]["name"] == "Oracle"
        assert client.put("/api/dungeon-master", json={**config, "capabilities": ["grant_lockpicking"]}).status_code == 422


def test_pirate_scenario_is_listed_selectable_and_resettable(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data" / "scenarios", data / "scenarios")
    shutil.copy(ROOT / "data" / "config.json", data / "config.json")
    monkeypatch.setattr(application, "DATA", data)
    with TestClient(application.app) as client:
        choices = client.get("/api/scenarios").json()["scenarios"]
        assert any(choice["id"] == "saltglass-passage" and
                   choice["title"] == "The Saltglass Passage" for choice in choices)
        initial = client.post("/api/control/start", json={"scenario_id": "saltglass-passage"}).json()
        assert initial["run"]["scenario_id"] == "saltglass-passage"
        assert initial["world"]["exit_location"] == "escape-quay"
        assert initial["run"]["goal"]["type"] == "entity_prepared"
        assert initial["run"]["config"]["budgets"] == {
            "max_actions": 300, "max_model_calls": 360, "max_seconds": 3600, "repetition_window": 3}
        assert initial["world"]["actors"][initial["run"]["actor_id"]]["body"]["capacity"] == 8
        assert initial["config"]["budgets"]["max_actions"] == 30
        assert initial["config"]["world"]["scenario"] == "escape-room"
        assert "shore-key" not in {entity["id"] for entity in initial["observation"]["visible_entities"]}
        configured = client.put("/api/config", json=initial["config"]).json()
        assert configured["run"]["config"]["budgets"] == initial["run"]["config"]["budgets"]
        reset = client.post("/api/control/reset", json={}).json()
        assert reset["run"]["id"] != initial["run"]["id"]
        assert reset["run"]["scenario_id"] == "saltglass-passage"
        assert reset["world"]["entities"]["signal-float"]["health"] == 30
        assert "rigging-mallet" not in reset["world"]["entities"]
        assert "ready-skiff" not in reset["world"]["entities"]
        assert reset["run"]["config"]["budgets"] == initial["run"]["config"]["budgets"]


def test_completed_attempt_has_grade_memory_and_manual_reassessment(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data" / "scenarios", data / "scenarios")
    shutil.copy(ROOT / "data" / "config.json", data / "config.json")
    monkeypatch.setattr(application, "DATA", data)
    with TestClient(application.app) as client:
        initial = client.post("/api/control/start", json={"scenario_id": "sandwich-kitchen"}).json()
        assert client.post("/api/control/assess", json={}).status_code == 409
        ended = client.post("/api/control/cancel", json={}).json()
        assert ended["run"]["status"] == "cancelled"
        assert ended["run"]["successful"] is False
        assert ended["run"]["completion_analysis"]["verdict"] == "fail"
        reassessed = client.post("/api/control/assess", json={}).json()
        assert reassessed["run"]["id"] == initial["run"]["id"]
        assert reassessed["run"]["completion_analysis"]["status"] == "completed"
        memory = read_json(data / "scenario-memory" / "sandwich-kitchen.json")
        assert len(memory["failures"]) == 1
        archived = client.get(f"/api/history/{initial['run']['id']}").json()
        assert archived["run"]["completion_analysis"]["story"] == reassessed["run"]["completion_analysis"]["story"]


def test_bakery_clock_api_validation_persistence_and_reset(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data" / "scenarios", data / "scenarios")
    shutil.copy(ROOT / "data" / "config.json", data / "config.json")
    monkeypatch.setattr(application, "DATA", data)
    with TestClient(application.app) as client:
        assert client.put("/api/clock", json={"rate": 120}).status_code == 409
        choices = client.get("/api/scenarios").json()["scenarios"]
        assert "bread-bakery" in {choice["id"] for choice in choices}
        initial = client.post("/api/control/start", json={"scenario_id": "bread-bakery"}).json()
        assert initial["world"]["clock"] == {
            "seconds": 0, "real_seconds": 0, "rate": 120, "running": False, "mode": "decision_paused"}
        for rate in (0, 601, "NaN", "Infinity"):
            assert client.put("/api/clock", json={"rate": rate}).status_code == 422
        updated = client.put("/api/clock", json={"rate": 240}).json()
        assert updated["world"]["clock"]["rate"] == 240
        assert updated["world"]["clock"]["seconds"] == 0
        assert client.get("/api/state").json()["world"]["clock"]["rate"] == 240
        assert client.get("/api/events").json()["events"][-1]["kind"] == "clock_rate"
        assert updated["world"]["clock"]["mode"] == "decision_paused"
        assert client.put("/api/clock", json={"rate": 120, "mode": "invalid"}).status_code == 422
        changed = client.put("/api/clock", json={"rate": 240, "mode": "continuous"}).json()
        assert changed["world"]["clock"]["mode"] == "continuous"
        assert client.put("/api/clock", json={"rate": 60}).json()["world"]["clock"]["mode"] == "continuous"
        reset = client.post("/api/control/reset", json={}).json()
        assert reset["world"]["clock"]["rate"] == 120
        assert reset["world"]["clock"]["seconds"] == 0
        assert reset["world"]["clock"]["mode"] == "decision_paused"
        client.post("/api/control/start", json={"scenario_id": "escape-room"})
        assert client.put("/api/clock", json={"rate": 120}).status_code == 409
