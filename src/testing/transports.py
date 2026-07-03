"""Transports that connect a TesterCore to a call.

  - ``LoopbackTransport`` — WebSocket client to our own /media-stream. Free,
    instant, tests all pipeline logic without touching the PSTN.
  - ``PstnBridge``       — drives the SERVER side of a real Telnyx media stream:
    the tester placed a real call from its own number; this bridge speaks and
    listens on that leg while the main agent answers on its production path.

Both do the same two jobs: pace out ``core.next_out_frame()`` every 20 ms, and
feed received agent audio into ``core.feed_agent()``.
"""

from __future__ import annotations

import asyncio
import audioop
import base64
import json

import structlog
import websockets

from src.testing.core import TesterCore, FRAME_BYTES

logger = structlog.get_logger()

FRAME_MS = 20


def _decoder(encoding: str):
    """Pick the inbound G.711 decoder from the stream's media_format."""
    if "PCMA" in (encoding or "").upper():
        return lambda b: audioop.alaw2lin(b, 2)
    return lambda b: audioop.ulaw2lin(b, 2)


class LoopbackTransport:
    """WS client that emulates Telnyx against our own server (A-law in, µ-law out)."""

    def __init__(self, core: TesterCore, url: str, call_id: str):
        self.core = core
        self.url = url
        self.call_id = call_id
        self.ws = None
        self._tasks: list[asyncio.Task] = []

    async def connect(self) -> None:
        self.ws = await websockets.connect(self.url, max_size=None)
        await self.ws.send(json.dumps({
            "event": "start",
            "start": {"call_control_id": self.call_id,
                      "media_format": {"encoding": "PCMA", "sample_rate": 8000}},
        }))
        self._tasks = [asyncio.create_task(self._line()),
                       asyncio.create_task(self._receiver())]

    async def _line(self) -> None:
        loop = asyncio.get_event_loop()
        next_t = loop.time()
        while True:
            pcm = self.core.next_out_frame()
            payload = base64.b64encode(audioop.lin2alaw(pcm, 2)).decode()
            try:
                await self.ws.send(json.dumps(
                    {"event": "media", "media": {"payload": payload}}))
            except Exception:  # noqa: BLE001
                return
            next_t += FRAME_MS / 1000
            delay = next_t - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                next_t = loop.time()

    async def _receiver(self) -> None:
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if msg.get("event") == "media":
                    self.core.feed_agent(audioop.ulaw2lin(
                        base64.b64decode(msg["media"]["payload"]), 2))
        except websockets.ConnectionClosed:
            pass

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        try:
            await self.ws.send(json.dumps({"event": "stop"}))
            await self.ws.close()
        except Exception:  # noqa: BLE001
            pass


class PstnBridge:
    """Server-side driver for the tester's leg of a REAL phone call."""

    def __init__(self, core: TesterCore, websocket, inbound_encoding: str = "PCMU"):
        self.core = core
        self.websocket = websocket           # FastAPI WebSocket (already accepted)
        self._decode = _decoder(inbound_encoding)
        self._tasks: list[asyncio.Task] = []
        self.closed = asyncio.Event()

    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._line()),
                       asyncio.create_task(self._receiver())]

    async def _line(self) -> None:
        loop = asyncio.get_event_loop()
        next_t = loop.time()
        while not self.closed.is_set():
            pcm = self.core.next_out_frame()
            payload = base64.b64encode(audioop.lin2ulaw(pcm, 2)).decode()
            try:
                await self.websocket.send_text(json.dumps(
                    {"event": "media", "media": {"payload": payload}}))
            except Exception:  # noqa: BLE001 — call leg ended
                self.closed.set()
                return
            next_t += FRAME_MS / 1000
            delay = next_t - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                next_t = loop.time()

    async def _receiver(self) -> None:
        try:
            while not self.closed.is_set():
                raw = await self.websocket.receive_text()
                msg = json.loads(raw)
                ev = msg.get("event")
                if ev == "media":
                    b64 = msg.get("media", {}).get("payload", "")
                    if b64:
                        self.core.feed_agent(self._decode(base64.b64decode(b64)))
                elif ev == "stop":
                    break
        except Exception as e:  # noqa: BLE001 — disconnect ends the leg
            logger.warning("pstn_bridge.receiver_ended", error=f"{type(e).__name__}: {e}")
        finally:
            self.closed.set()

    async def stop(self) -> None:
        self.closed.set()
        for t in self._tasks:
            t.cancel()
