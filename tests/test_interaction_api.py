import shutil
from pathlib import Path

from fastapi.testclient import TestClient

import npc_sim.app as application
from npc_sim.interactions import NpcReply
from npc_sim.proxy import Proxy
from npc_sim.storage import read_json, write_json
from test_interactions import nook_plan


ROOT = Path(__file__).resolve().parent.parent


def test_private_chat_and_approved_world_changes_api(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data" / "scenarios", data / "scenarios")
    config = read_json(ROOT / "data" / "config.json")
    config["proxy"]["model"] = "test-model"
    write_json(data / "config.json", config)
    monkeypatch.setattr(application, "DATA", data)

    async def answer(self, *args):
        return NpcReply(answer="I have not found that key."), {}

    async def propose(self, *args):
        return nook_plan(), {}

    monkeypatch.setattr(Proxy, "answer_question", answer)
    monkeypatch.setattr(Proxy, "propose_world_edit", propose)
    with TestClient(application.app) as client:
        initial = client.post("/api/control/start", json={}).json()
        run_id = initial["run"]["id"]
        actor_id = initial["run"]["actor_id"]
        request = {"run_id": run_id, "actor_id": actor_id, "question": "Have you found a key?"}
        response = client.post("/api/interactions/chat", json=request)
        assert response.status_code == 200
        assert response.json()["world"] == initial["world"]
        assert response.json()["memory"]["operator_conversation"][-1]["answer"] == "I have not found that key."
        assert client.post("/api/interactions/chat", json={**request, "question": "  "}).status_code == 422
        assert client.post("/api/interactions/chat", json={**request, "actor_id": "foreign"}).status_code == 409
        assert client.post("/api/interactions/chat", json={
            "run_id": run_id, "instruction": "Wrong operation"}).status_code == 422
        assert client.post("/api/interactions/unknown", json=request).status_code == 404
        proposed = client.post("/api/interactions/propose", json={
            "run_id": run_id, "instruction": "Add a hidden nook with a spare door key."})
        assert proposed.status_code == 200
        assert proposed.json()["world"] == initial["world"]
        proposal = next(iter(proposed.json()["run"]["world_edit_proposals"].values()))
        client.put(f"/api/actors/{actor_id}/needs/sleep", json={"enabled": True})
        before = client.get("/api/state").json()["world"]
        assert client.post("/api/interactions/approve", json={
            "run_id": run_id, "proposal_id": proposal["id"]}).status_code == 409
        assert client.get("/api/state").json()["world"] == before
        fresh = client.post("/api/interactions/propose", json={
            "run_id": run_id, "instruction": "Add the hidden nook and spare key."}).json()
        proposal = list(fresh["run"]["world_edit_proposals"].values())[-1]
        approved = client.post("/api/interactions/approve", json={"run_id": run_id, "proposal_id": proposal["id"]})
        assert approved.status_code == 200
        assert approved.json()["world"]["entities"]["rescue-nook"]["hidden"]
        assert "rescue-nook" not in str(approved.json()["observation"])
        assert client.post("/api/interactions/approve", json={
            "run_id": run_id, "proposal_id": proposal["id"]}).status_code == 409

        async def fail(self, *args):
            raise RuntimeError("Question provider unavailable")
        monkeypatch.setattr(Proxy, "answer_question", fail)
        failure = client.post("/api/interactions/chat", json=request)
        assert failure.status_code == 502
        assert "Question provider unavailable" in failure.json()["detail"]
        assert not client.get("/api/state").json()["in_flight"]


def test_goal_only_intervention_api_requires_approval_and_keeps_primary_goal(tmp_path, monkeypatch):
    from test_secondary_goals import goal_plan
    from test_controller import assessment_response
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data" / "scenarios", data / "scenarios")
    config = read_json(ROOT / "data" / "config.json")
    config["proxy"]["model"] = "test-model"
    write_json(data / "config.json", config)
    monkeypatch.setattr(application, "DATA", data)

    async def propose(self, *args):
        return goal_plan(actor_ids=["npc-001"]), {}

    async def assess(self, model, messages, correlation_id, timeout):
        return assessment_response(messages), {}

    monkeypatch.setattr(Proxy, "propose_world_edit", propose)
    monkeypatch.setattr(Proxy, "assess", assess)
    with TestClient(application.app) as client:
        initial = client.post("/api/control/start", json={}).json()
        run_id = initial["run"]["id"]
        proposed = client.post("/api/interactions/propose", json={
            "run_id": run_id, "instruction": "Give Player an optional goal to find the key."})
        assert proposed.status_code == 200
        assert proposed.json()["world"] == initial["world"]
        proposal = next(iter(proposed.json()["run"]["world_edit_proposals"].values()))
        assert proposal["plan"]["additions"] == {}
        assert proposal["plan"]["secondary_goals"]["find-key"]["assigned_actor_ids"] == ["npc-001"]
        approved = client.post("/api/interactions/approve", json={"run_id": run_id, "proposal_id": proposal["id"]})
        assert approved.status_code == 200
        final = approved.json()
        assert final["run"]["goal"] == initial["run"]["goal"]
        assert final["world"]["entities"] == initial["world"]["entities"]
        assert final["world"]["secondary_goals"]["find-key"]["status"] == "active"
        assert final["observation"]["secondary_goals"][0]["id"] == "find-key"
