"""Tests for outbound event webhooks (mocked transport, no network)."""

import hashlib
import hmac
import json

import httpx

from src.integrations.webhooks import post_event, sign


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_posts_signed_payload():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["sig"] = request.headers.get("x-signature-sha256")
        seen["body"] = bytes(request.content)
        return httpx.Response(200, json={"ok": True})

    payload = {"call_id": "c1", "outcome": "booked"}
    ok = await post_event("https://crm.example.com/hook", payload,
                          secret="s3cr3t", client=_client(handler))
    assert ok is True
    assert seen["url"] == "https://crm.example.com/hook"
    # signature matches HMAC of the exact body sent
    assert seen["sig"] == hmac.new(b"s3cr3t", seen["body"], hashlib.sha256).hexdigest()
    assert json.loads(seen["body"])["call_id"] == "c1"


async def test_no_signature_header_without_secret():
    seen = {}

    def handler(request):
        seen["sig"] = request.headers.get("x-signature-sha256")
        return httpx.Response(204)

    ok = await post_event("https://x/y", {"a": 1}, client=_client(handler))
    assert ok is True                     # 204 is 2xx
    assert seen["sig"] is None


async def test_non_2xx_returns_false():
    def handler(request):
        return httpx.Response(500)
    assert await post_event("https://x/y", {}, client=_client(handler)) is False


async def test_transport_error_returns_false():
    def handler(request):
        raise httpx.ConnectError("boom")
    assert await post_event("https://x/y", {}, client=_client(handler)) is False


async def test_empty_url_is_noop():
    async def boom(*a, **k):
        raise AssertionError("should not post")
    assert await post_event("", {"a": 1}) is False


def test_sign_is_stable():
    assert sign("k", b"body") == hmac.new(b"k", b"body", hashlib.sha256).hexdigest()
