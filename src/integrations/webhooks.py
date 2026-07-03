"""Outbound event webhooks — notify a business's systems about call events.

When a call completes, the platform POSTs the call record (+ summary) to the
agent's configured ``webhook_url`` so the business can sync it into a CRM,
trigger follow-ups, etc. Payloads are HMAC-SHA256 signed (``x-signature-sha256``)
when a secret is set, so the receiver can verify authenticity.

Delivery is best-effort and isolated: a slow or failing endpoint must never
affect the call. Bounded by a timeout; the client is injectable for tests.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import structlog

logger = structlog.get_logger()


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def post_event(url: str, payload: dict, *, secret: str = "",
                     client: httpx.AsyncClient | None = None,
                     timeout: float = 10.0) -> bool:
    """POST ``payload`` as JSON to ``url``. Returns True on a 2xx response, False
    on any error (logged, never raised)."""
    if not url:
        return False
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"content-type": "application/json", "user-agent": "voice-agent/1.0"}
    if secret:
        headers["x-signature-sha256"] = sign(secret, body)
    try:
        if client is not None:
            resp = await client.post(url, content=body, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=timeout) as c:
                resp = await c.post(url, content=body, headers=headers)
        ok = 200 <= resp.status_code < 300
        if not ok:
            logger.warning("webhook.non_2xx", url=url, status=resp.status_code)
        return ok
    except Exception as e:  # noqa: BLE001 — delivery failure must not affect the call
        logger.warning("webhook.delivery_failed", url=url, error=str(e))
        return False
