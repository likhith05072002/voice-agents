"""Persistent TTS connection with keep-alive pings.

The problem: Sarvam closes idle TTS WebSocket after ~30-60 seconds.
The fix: Send ping every 15 seconds AND reconnect instantly on failure.

This eliminates the 1100ms fresh-connection overhead per response.
"""

import asyncio
import base64
import json
import time

import structlog
import websockets as ws_lib

from src.config import settings

logger = structlog.get_logger()

SARVAM_API_KEY = settings.sarvam_api_key


class PersistentTTS:
    """TTS WebSocket that stays alive with keep-alive pings."""

    def __init__(self):
        self._ws = None
        self._connected = False
        self._ping_task = None
        self._config = {
            "type": "config",
            "data": {
                "target_language_code": settings.default_language,
                "speaker": settings.sarvam_tts_voice,
                "model": settings.sarvam_tts_model,
                "speech_sample_rate": "8000",
                "output_audio_codec": "linear16",
                "send_completion_event": True,
            },
        }

    async def connect(self):
        """Connect and start keep-alive pings."""
        try:
            self._ws = await ws_lib.connect(
                "wss://api.sarvam.ai/text-to-speech/ws",
                additional_headers={"api-subscription-key": SARVAM_API_KEY},
            )
            await self._ws.send(json.dumps(self._config))
            self._connected = True

            # Start keep-alive ping loop
            if self._ping_task:
                self._ping_task.cancel()
            self._ping_task = asyncio.create_task(self._ping_loop())

            logger.info("persistent_tts.connected")
        except Exception as e:
            logger.error("persistent_tts.connect_error", error=str(e))
            self._connected = False

    async def _ping_loop(self):
        """Send ping every 15 seconds to keep connection alive."""
        try:
            while self._connected:
                await asyncio.sleep(15)
                if self._ws and self._connected:
                    try:
                        await self._ws.send(json.dumps({"type": "ping"}))
                    except Exception:
                        logger.warning("persistent_tts.ping_failed")
                        self._connected = False
                        break
        except asyncio.CancelledError:
            pass

    async def _ensure_connected(self):
        """Reconnect if connection died."""
        if not self._connected or not self._ws:
            await self.connect()

    async def synthesize(self, text: str) -> list[bytes]:
        """Synthesize text and return all audio chunks as PCM16 8kHz.
        Handles reconnection transparently."""
        await self._ensure_connected()

        try:
            await self._ws.send(json.dumps({"type": "text", "data": {"text": text}}))
            await self._ws.send(json.dumps({"type": "flush"}))

            chunks = []
            while True:
                try:
                    raw = await asyncio.wait_for(self._ws.recv(), timeout=8.0)
                    msg = json.loads(raw)
                    if msg.get("type") == "audio":
                        chunks.append(base64.b64decode(msg["data"]["audio"]))
                    elif msg.get("type") == "event":
                        break  # Completion event
                    elif msg.get("type") == "error":
                        logger.error("persistent_tts.synthesis_error", msg=msg.get("data", {}).get("message"))
                        break
                except asyncio.TimeoutError:
                    break

            return chunks

        except Exception as e:
            logger.warning("persistent_tts.send_failed", error=str(e))
            self._connected = False
            # Reconnect and retry once
            await self.connect()
            try:
                await self._ws.send(json.dumps({"type": "text", "data": {"text": text}}))
                await self._ws.send(json.dumps({"type": "flush"}))

                chunks = []
                while True:
                    try:
                        raw = await asyncio.wait_for(self._ws.recv(), timeout=8.0)
                        msg = json.loads(raw)
                        if msg.get("type") == "audio":
                            chunks.append(base64.b64decode(msg["data"]["audio"]))
                        elif msg.get("type") in ("event", "error"):
                            break
                    except asyncio.TimeoutError:
                        break
                return chunks
            except Exception as e2:
                logger.error("persistent_tts.retry_failed", error=str(e2))
                return []

    async def close(self):
        self._connected = False
        if self._ping_task:
            self._ping_task.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
