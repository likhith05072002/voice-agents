"""Guards for the Sarvam LLM request payload.

The single most important property: reasoning_effort must be sent as JSON null
by default. Sarvam enables "thinking" by default; omitting the key (or sending a
value) re-enables it and pushes first-token latency from ~340ms to ~8s. These
tests lock that in so a future refactor can't silently regress TTFT.
"""

import json

from src.services.llm.sarvam import SarvamLLMClient, extract_json


def test_reasoning_disabled_by_default():
    c = SarvamLLMClient("k")
    p = c._payload([{"role": "user", "content": "hi"}])
    assert "reasoning_effort" in p           # key MUST be present
    assert p["reasoning_effort"] is None     # None -> JSON null -> reasoning OFF
    assert p["model"] == "sarvam-30b"
    assert p["stream"] is True


def test_null_actually_serializes_in_json_body():
    # The wire format is what matters — assert literal `null`, not an omission.
    body = json.dumps(SarvamLLMClient("k")._payload([]))
    assert '"reasoning_effort": null' in body


def test_explicit_effort_and_model_are_respected():
    c = SarvamLLMClient("k", model="sarvam-105b", reasoning_effort="low", max_tokens=128)
    p = c._payload([])
    assert p["reasoning_effort"] == "low"
    assert p["model"] == "sarvam-105b"
    assert p["max_tokens"] == 128


def test_complete_payload_is_non_streaming_and_carries_tools():
    c = SarvamLLMClient("k")
    tools = [{"type": "function", "function": {"name": "t", "description": "", "parameters": {}}}]
    p = c._complete_payload([{"role": "user", "content": "hi"}], tools)
    assert p["stream"] is False
    assert p["reasoning_effort"] is None        # still disabled for tool decisions
    assert p["tools"] == tools
    # no tools -> key omitted
    assert "tools" not in c._complete_payload([])


def test_extract_json_plain():
    assert extract_json('{"intent": "price", "karat": 22}') == {"intent": "price", "karat": 22}


def test_extract_json_from_prose_and_fences():
    text = 'Sure! ```json\n{"a": 1, "b": {"c": 2}}\n``` hope that helps'
    assert extract_json(text) == {"a": 1, "b": {"c": 2}}


def test_extract_json_invalid_or_missing_returns_empty():
    assert extract_json("no json here") == {}
    assert extract_json('{"broken": ') == {}
    assert extract_json("[1,2,3]") == {}        # array is not a dict


async def test_warmup_tolerates_connection_error():
    import httpx

    def boom(request):
        raise httpx.ConnectError("down")

    c = SarvamLLMClient("k")
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    await c.warmup()                 # must not raise
    assert c._client is not None


async def test_warmup_issues_a_request_when_reachable():
    import httpx
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={})

    c = SarvamLLMClient("k")
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await c.warmup()
    assert seen and seen[0].startswith(c.base_url)
