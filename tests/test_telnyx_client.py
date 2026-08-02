"""Tests for the Telnyx Call Control client (mocked transport, no network)."""

import json

import httpx
import pytest

from src.telephony.telnyx import TelnyxClient


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_create_call_returns_call_control_id():
    seen = []

    def handler(request):
        seen.append((request.method, str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={"data": {"call_control_id": "cc-123"}})

    tc = TelnyxClient("key", client=_client(handler))
    ccid = await tc.create_call(to="+15551112222", from_="+15553334444",
                                connection_id="conn-1", client_state="ctx")
    assert ccid == "cc-123"
    method, url, body = seen[0]
    assert method == "POST" and url.endswith("/v2/calls")
    assert body["to"] == "+15551112222"
    assert body["connection_id"] == "conn-1"
    assert body["client_state"] == "ctx"
    assert body["from"] == "+15553334444"


async def test_answer_and_streaming_start_paths():
    seen = []

    def handler(request):
        seen.append((str(request.url), json.loads(request.content) if request.content else {}))
        return httpx.Response(200, json={})

    tc = TelnyxClient("k", client=_client(handler))
    await tc.answer("cc1")
    await tc.streaming_start("cc1", stream_url="wss://host/media-stream")

    urls = [u for u, _ in seen]
    assert any(u.endswith("/calls/cc1/actions/answer") for u in urls)
    ss = next(b for u, b in seen if "streaming_start" in u)
    assert ss["stream_url"] == "wss://host/media-stream"
    assert ss["stream_bidirectional_codec"] == "PCMU"


async def test_create_call_raises_on_error_status():
    def handler(request):
        return httpx.Response(422, json={"errors": [{"detail": "bad number"}]})

    tc = TelnyxClient("k", client=_client(handler))
    with pytest.raises(RuntimeError):
        await tc.create_call(to="+1", from_="+2", connection_id="c")


async def test_hangup_path():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={})

    tc = TelnyxClient("k", client=_client(handler))
    await tc.hangup("cc9")
    assert seen[0].endswith("/calls/cc9/actions/hangup")


async def test_streaming_start_raises_on_rejection():
    """A rejected streaming_start must NOT pass silently: the call connects
    and the caller hears pure silence, which is the hardest symptom to
    diagnose (cost a live debugging session). Fail loudly instead."""
    def handler(request):
        return httpx.Response(422, json={"errors": [{"detail": "bad stream_url"}]})

    tc = TelnyxClient("key", client=_client(handler))
    with pytest.raises(RuntimeError, match="streaming_start failed: 422"):
        await tc.streaming_start("cc-1", stream_url="wss://example.test/media-stream")
