"""Telnyx Call Control REST client.

Wraps the call-control actions the platform uses for both inbound (answer →
stream) and outbound (create → answer → stream). Extracted from the webhook glue
so it is reusable and unit-testable (inject an ``httpx.AsyncClient`` with a
``MockTransport``; no network in tests).

Reference: Telnyx Call Control v2 (`/v2/calls`, `/v2/calls/{id}/actions/*`).
"""

from __future__ import annotations

import httpx
import structlog

logger = structlog.get_logger()


class TelnyxClient:
    def __init__(self, api_key: str, *, base_url: str = "https://api.telnyx.com/v2",
                 client: httpx.AsyncClient | None = None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = client          # injected for tests / connection reuse

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def _post(self, path: str, body: dict) -> httpx.Response:
        url = f"{self.base_url}{path}"
        if self._client is not None:
            return await self._client.post(url, headers=self._headers, json=body)
        async with httpx.AsyncClient(timeout=15.0) as c:
            return await c.post(url, headers=self._headers, json=body)

    async def answer(self, call_control_id: str) -> None:
        await self._post(f"/calls/{call_control_id}/actions/answer", {})

    async def hangup(self, call_control_id: str) -> None:
        await self._post(f"/calls/{call_control_id}/actions/hangup", {})

    async def streaming_start(self, call_control_id: str, *, stream_url: str,
                              track: str = "inbound_track", codec: str = "PCMU") -> None:
        """Ask Telnyx to open the media WebSocket back to us.

        The response IS checked: a rejected streaming_start used to fail
        silently, and the caller hears a perfectly connected call with total
        silence — the hardest possible symptom to diagnose. Log it loudly."""
        resp = await self._post(
            f"/calls/{call_control_id}/actions/streaming_start",
            {
                "stream_url": stream_url,
                "stream_track": track,
                "stream_bidirectional_mode": "rtp",
                "stream_bidirectional_codec": codec,
            },
        )
        if resp.status_code >= 300:
            logger.error("telnyx.streaming_start_failed",
                         status=resp.status_code, url=stream_url,
                         body=resp.text[:300])
            raise RuntimeError(f"streaming_start failed: {resp.status_code}")
        logger.info("telnyx.streaming_started", url=stream_url)

    async def transfer(self, call_control_id: str, *, to: str, from_: str = "") -> None:
        """Transfer the live call to another number (human handoff). The PSTN
        leg is bridged by Telnyx; our media stream ends when the transfer
        completes."""
        body: dict = {"to": to}
        if from_:
            body["from"] = from_
        resp = await self._post(f"/calls/{call_control_id}/actions/transfer", body)
        if resp.status_code >= 300:
            logger.error("telnyx.transfer_failed", status=resp.status_code,
                         body=resp.text[:200])
            raise RuntimeError(f"transfer failed: {resp.status_code}")

    async def create_call(self, *, to: str, from_: str, connection_id: str,
                          client_state: str | None = None) -> str:
        """Place an outbound call. Returns the new ``call_control_id``."""
        body: dict = {"to": to, "from": from_, "connection_id": connection_id}
        if client_state is not None:
            body["client_state"] = client_state
        resp = await self._post("/calls", body)
        if resp.status_code >= 300:
            logger.error("telnyx.create_call_failed", status=resp.status_code,
                         body=resp.text[:200])
            raise RuntimeError(f"create_call failed: {resp.status_code}")
        return resp.json()["data"]["call_control_id"]
