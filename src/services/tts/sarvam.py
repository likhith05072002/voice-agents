"""Sarvam Bulbul V2 Streaming TTS Client over WebSocket."""

import asyncio
import base64
import json
import time

import structlog
import websockets

logger = structlog.get_logger()


class SarvamTTSClient:
    """Streaming Text-to-Speech via Sarvam Bulbul WebSocket.

    Outputs mulaw 8kHz audio — ready for telephony with zero resampling.
    """

    def __init__(self, api_key: str, model: str = "bulbul:v2"):
        self.api_key = api_key
        self.model = model
        self._ws = None
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._receive_task = None
        self._first_audio_time: float | None = None
        self._send_time: float | None = None

    async def connect(
        self,
        language: str = "te-IN",
        voice: str = "meera",
        sample_rate: str = "8000",
    ) -> None:
        self._ws = await websockets.connect(
            "wss://api.sarvam.ai/text-to-speech/ws",
            additional_headers={"api-subscription-key": self.api_key},
            ping_interval=20,
            ping_timeout=10,
        )

        config = {
            "type": "config",
            "data": {
                "target_language_code": language,
                "speaker": voice,
                "model": self.model,
                "speech_sample_rate": sample_rate,
                "output_audio_codec": "linear16",
                "send_completion_event": True,
            },
        }
        await self._ws.send(json.dumps(config))
        self._receive_task = asyncio.create_task(self._receive_loop())
        self._first_audio_time = None
        logger.info("tts.connected", language=language, voice=voice, model=self.model)

    async def send_text(self, text: str) -> None:
        if not self._ws:
            return
        self._send_time = time.perf_counter()
        msg = json.dumps({"type": "text", "data": {"text": text}})
        await self._ws.send(msg)

    async def flush(self) -> None:
        if self._ws:
            await self._ws.send(json.dumps({"type": "flush"}))

    async def get_audio(self) -> bytes | None:
        """Get next audio chunk. Returns None when synthesis is complete."""
        return await self._audio_queue.get()

    async def _receive_loop(self) -> None:
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(self._ws.recv(), timeout=5.0)
                except asyncio.TimeoutError:
                    # No more audio coming — signal completion
                    await self._audio_queue.put(None)
                    break

                msg = json.loads(raw)
                msg_type = msg.get("type")

                if msg_type == "audio":
                    audio_b64 = msg["data"]["audio"]
                    audio_bytes = base64.b64decode(audio_b64)

                    if self._first_audio_time is None:
                        self._first_audio_time = time.perf_counter()
                        if self._send_time:
                            ttfa = (self._first_audio_time - self._send_time) * 1000
                            logger.info("tts.first_audio", ttfa_ms=round(ttfa))

                    await self._audio_queue.put(audio_bytes)

                elif msg_type == "event":
                    event_type = msg.get("data", {}).get("event_type")
                    if event_type == "final":
                        await self._audio_queue.put(None)

                elif msg_type == "error":
                    logger.error("tts.error", msg=msg.get("data", {}).get("message"))
                    await self._audio_queue.put(None)

        except websockets.ConnectionClosed:
            logger.info("tts.connection_closed")
        except Exception as e:
            logger.error("tts.receive_error", error=str(e))
        finally:
            await self._audio_queue.put(None)

    async def close(self) -> None:
        if self._receive_task:
            self._receive_task.cancel()
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("tts.closed")

    async def reset(self) -> None:
        """Reset for next utterance without reconnecting."""
        self._first_audio_time = None
        self._send_time = None
        # Drain any leftover audio
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    @property
    def is_connected(self) -> bool:
        if self._ws is None:
            return False
        try:
            return self._ws.state.name == "OPEN"
        except Exception:
            return False
