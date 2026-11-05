import json

import httpx
import pytest
from pydantic import ValidationError

from npc_sim.models import ProxyConfig
from npc_sim.proxy import Proxy


@pytest.mark.asyncio
async def test_openai_proxy_contract_and_no_auth(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "local-model"}]})
        body = json.loads(request.content)
        assert body["model"] == "local-model"
        assert body["stream"] is False
        return httpx.Response(200, json={
            "model": "local-model", "usage": {"total_tokens": 10},
            "choices": [{"message": {"content": json.dumps({
                "action_id": "look", "arguments": {}, "justification": "Observe my surroundings.",
                "expected_outcome": "Discover relevant objects.", "subgoal": "Explore", "plan": []})}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(handler)))
    proxy = Proxy(ProxyConfig())
    assert await proxy.models() == ["local-model"]
    messages = proxy.context({"objective": "Leave the room."}, {"visible_entities": []}, {"recent": []})
    decision, metadata = await proxy.choose("local-model", messages, "run:cycle", 10)
    assert decision.action_id == "look"
    assert metadata["usage"]["total_tokens"] == 10
    assert all(request.url.host == "127.0.0.1" and request.url.port == 9901 for request in seen)
    assert all("authorization" not in request.headers for request in seen)
    assert seen[-1].headers["x-request-id"] == "run:cycle"


@pytest.mark.asyncio
async def test_malformed_model_output_is_not_a_decision(monkeypatch):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, json={"choices": [{"message": {"content": '{"action_id": "invented"}'}}]}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=transport))
    with pytest.raises(ValidationError):
        await Proxy(ProxyConfig()).choose("local-model", [], "request", 5)


@pytest.mark.asyncio
async def test_completion_review_uses_the_same_local_proxy_and_structured_reasons(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.host == "127.0.0.1" and request.url.port == 9901
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["x-request-id"] == "run:assessment"
        body = json.loads(request.content)
        assert body["model"] == "local-model" and body["stream"] is False
        assert "untrusted data" in body["messages"][0]["content"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "verdict": "fail", "findings": [{"reason": "The sandwich remains on the worktop.",
                                          "evidence_ids": ["run:1"]}]})}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(handler)))
    proxy = Proxy(ProxyConfig())
    messages = proxy.completion_context({
        "objective": "Serve the sandwich on the plate.",
        "story": [{"id": "run:1", "text": "The sandwich remains on the worktop."}],
        "final_verification": {"result": {"status": "active"}, "event_id": "run:2"},
    })
    review, _ = await proxy.assess("local-model", messages, "run:assessment", 5)
    assert review.verdict == "fail"
    assert review.findings[0].evidence_ids == ["run:1"]
    assert len(seen) == 1


def test_missing_token_environment_is_explicit(monkeypatch):
    monkeypatch.delenv("NPCSIM_TEST_TOKEN", raising=False)
    with pytest.raises(ValueError, match="Missing token environment"):
        Proxy(ProxyConfig(token_environment="NPCSIM_TEST_TOKEN")).headers()
