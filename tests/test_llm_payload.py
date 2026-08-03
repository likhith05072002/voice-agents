"""Guards for the Sarvam LLM request payload.

The single most important property FLIPPED on 2026-08-03: reasoning_effort
used to be sent as JSON null to disable thinking (their documented
low-latency mode) — then Sarvam's gateway started HANGING FOREVER on null
(validator now accepts only low/medium/high), which took every call down.
The key must now be OMITTED when unset, and never serialized as null.
"""

import json

from src.services.llm.sarvam import SarvamLLMClient, extract_json


def test_reasoning_effort_omitted_by_default():
    c = SarvamLLMClient("k")
    p = c._payload([{"role": "user", "content": "hi"}])
    assert "reasoning_effort" not in p       # null = infinite hang since 2026-08-03
    assert p["model"] == "sarvam-105b"
    assert p["stream"] is True


def test_null_never_reaches_the_wire():
    # The wire format is what matters — the literal `null` is what hangs them.
    body = json.dumps(SarvamLLMClient("k")._payload([]))
    assert '"reasoning_effort": null' not in body


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
    assert "reasoning_effort" not in p          # never null here either
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
