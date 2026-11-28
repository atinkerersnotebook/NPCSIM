import json

import httpx
import pytest
from pydantic import ValidationError

from npc_sim.models import ProxyConfig
from npc_sim.proxy import EmptyModelResponse, Proxy


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
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["name"] == "npcsim_decision"
        assert body["response_format"]["json_schema"]["strict"] is True
        decision_schema = body["response_format"]["json_schema"]["schema"]
        assert decision_schema["properties"]["plan"]["items"] == {"type": "string"}
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
    assert metadata["compression"]["effective_mode"] == "normal"
    assert all(request.url.host == "127.0.0.1" and request.url.port == 9901 for request in seen)
    assert all("authorization" not in request.headers for request in seen)
    assert seen[-1].headers["x-request-id"] == "run:cycle"


@pytest.mark.asyncio
async def test_models_normalizes_windows_url_separator(monkeypatch):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"data": [{"id": "local-model"}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(handler)))
    proxy = Proxy(ProxyConfig(base_url=r"http://127.0.0.1:9901\v1"))

    assert await proxy.models() == ["local-model"]
    assert seen == ["http://127.0.0.1:9901/v1/models"]


@pytest.mark.asyncio
async def test_action_context_is_structured_text_generation(monkeypatch):
    seen = []
    original = httpx.AsyncClient

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json={
            "model": "story-model",
            "usage": {"total_tokens": 8},
            "choices": [{"message": {"content": '{"description":"The verified result leaves a clear next lead."}'}}],
        })

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(handler)))
    proxy = Proxy(ProxyConfig(model="story-model", action_context_mode="both"))
    messages = proxy.action_context_context(
        actor="otter", action={"action_id": "look", "arguments": {}},
        result={"request_id": "r1", "outcome": "APPLIED", "code": "OBSERVED",
                "explanation": "A key is visible.", "observation": {}},
        memory={"recent": []})
    context, metadata = await proxy.action_context("story-model", messages, "context", 5)
    assert context.description == "The verified result leaves a clear next lead."
    assert metadata["model"] == "story-model"
    assert seen[0]["response_format"]["json_schema"]["name"] == "npcsim_action_context"


@pytest.mark.asyncio
async def test_adjudication_context_preserves_authoritative_ruling(monkeypatch):
    seen = []
    original = httpx.AsyncClient

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={
            "model": "story-model",
            "choices": [{"message": {"content": '{"description":"The world **rejected** the action because the `tool` was absent."}'}}],
        })

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(handler)))
    proxy = Proxy(ProxyConfig(model="story-model"))
    ruling = {"name": "Supervisor", "scope": "action", "verdict": "rejected",
              "reason": "The tool is not present.", "source": "world"}
    messages = proxy.adjudication_context_context(ruling=ruling)
    context, _ = await proxy.adjudication_context("story-model", messages, "ruling", 5)
    assert context.description == "The world rejected the action because the tool was absent."
    assert seen[0]["response_format"]["json_schema"]["name"] == "npcsim_adjudication_context"


@pytest.mark.asyncio
async def test_compressed_proxy_request_reports_prompt_savings(monkeypatch):
    original = httpx.AsyncClient
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={
            "model": "local-model", "usage": {
                "prompt_tokens": 120,
                "prompt_tokens_details": {"cached_tokens": 40},
            },
            "choices": [{"message": {"content": json.dumps({
                "action_id": "look", "arguments": {}, "justification": "Observe.",
                "expected_outcome": "Learn more.", "subgoal": "Explore", "plan": []})}}]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(handler)))
    proxy = Proxy(ProxyConfig(message_format="fact-shorthand", message_deduplicate=True))
    messages = proxy.context({"objective": "Collect debt"}, {"visible_entities": []}, {"facts": [{
        "type": "debt", "debtor": "Arin", "creditor": "Mara", "amount": 3,
        "currency": "silver", "source": "M-12",
    }]})
    _, metadata = await proxy.choose("local-model", messages, "run:compressed", 5)
    assert seen[0]["messages"][1]["content"].startswith('{"compression"')
    assert metadata["compression"]["effective_mode"] == "fact-shorthand"
    assert metadata["compression"]["options"] == {
        "deduplicate": True, "relevant_memory": False,
        "aggregate_events": False, "perception_delta": False,
    }
    assert metadata["compression"]["facts_encoded"] == 1
    assert metadata["compression"]["cache_read_tokens"] == 40
    assert metadata["compression"]["uncached_prompt_tokens"] == 80
    assert metadata["compression"]["cache_hit_ratio"] == pytest.approx(1 / 3)
    assert metadata["compression"]["cache_status"] == "reported"


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
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["name"] == "npcsim_story_review"
        assert "literal label" in body["messages"][0]["content"]
        assert "final_verification.event_id" in body["messages"][0]["content"]
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


@pytest.mark.asyncio
async def test_dungeon_master_review_constrains_citations_to_supplied_ids(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        body = json.loads(request.content)
        assert body["response_format"]["json_schema"]["name"] == "npcsim_dungeon_master_review"
        schema = body["response_format"]["json_schema"]["schema"]
        assert schema["$defs"]["Finding"]["properties"]["evidence_ids"]["items"]["enum"] == ["run:1", "run:2"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "verdict": "continue", "findings": [{"reason": "Work remains possible.", "evidence_ids": ["run:1"]}]})}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(handler)))
    proxy = Proxy(ProxyConfig())
    messages = proxy.dungeon_master_context({
        "config": {}, "goal": {"objective": "Continue"}, "team": {},
        "evidence": [{"id": "run:1", "kind": "verification", "payload": {"status": "active"}},
                     {"id": "run:2", "kind": "action_result", "payload": {"outcome": "APPLIED"}}],
    })
    review, _ = await proxy.review_progress("local-model", messages, "run:dm", 5)
    assert review.findings[0].evidence_ids == ["run:1"]
    assert len(seen) == 1


def test_long_completion_story_keeps_start_recent_events_and_valid_evidence_ids():
    proxy = Proxy(ProxyConfig())
    story = [{"id": f"event:{index}", "text": "A confirmed result. " * 20}
             for index in range(5103)]
    payload = {"objective": "Complete the village day.", "story": story,
               "final_verification": {"event_id": "verification:1", "result": {"status": "active"}}}
    messages = proxy.completion_context(payload)
    supplied = json.loads(messages[1]["content"])
    assert len(messages[1]["content"]) < 50000
    assert supplied["story"][0]["id"] == "event:0"
    assert supplied["story"][-1]["id"] == "event:5102"
    assert len(supplied["story"]) == 80
    assert supplied["final_verification"] == payload["final_verification"]
    assert len(payload["story"]) == 5103


@pytest.mark.asyncio
async def test_empty_completion_review_is_reported_explicitly(monkeypatch):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, json={"choices": [{"message": {"content": ""}}]}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=transport))
    with pytest.raises(EmptyModelResponse, match="empty completion review"):
        await Proxy(ProxyConfig()).assess("local-model", [], "request", 5)


def test_missing_token_environment_is_explicit(monkeypatch):
    monkeypatch.delenv("NPCSIM_TEST_TOKEN", raising=False)
    with pytest.raises(ValueError, match="Missing token environment"):
        Proxy(ProxyConfig(token_environment="NPCSIM_TEST_TOKEN")).headers()
