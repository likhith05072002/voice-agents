"""Sarvam Bulbul V2 Streaming TTS Client over WebSocket."""

import asyncio
import base64
import json
import time

import structlog
import websockets

from src.util.backoff import retry_async

logger = structlog.get_logger()

# Bulbul speakers, from the live API's own error listing (2026-07). Used only to
# warn on an unknown voice — never to block, so new speakers still work.
KNOWN_VOICES = frozenset({
    "anushka", "abhilash", "manisha", "vidya", "arya", "karun", "hitesh",
    "aditya", "ritu", "priya", "neha", "rahul", "pooja", "rohan", "simran",
    "kavya", "amit", "dev", "ishita", "shreya", "ratan", "varun", "manan",
    "sumit", "roopa", "kabir", "aayan", "shubh", "ashutosh", "advait", "anand",
    "tanya", "tarun", "sunny", "mani", "gokul", "vijay", "shruti", "suhani",
    "mohit", "kavitha", "rehan", "soham", "rupali",
})


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
        self._language: str = "te-IN"
        self._voice: str = "anushka"
        self._sample_rate: str = "8000"

    async def connect(
        self,
        language: str = "te-IN",
        voice: str = "anushka",
        sample_rate: str = "8000",
    ) -> None:
        self._language = language
        self._voice = voice
        self._sample_rate = sample_rate
        if self._receive_task:
            self._receive_task.cancel()
        async def _open():
            return await websockets.connect(
                "wss://api.sarvam.ai/text-to-speech/ws",
                additional_headers={"api-subscription-key": self.api_key},
                ping_interval=20,
                ping_timeout=10,
            )

        self._ws = await retry_async(
            _open, attempts=3, base_delay=0.2,
            on_retry=lambda a, e: logger.warning("tts.connect_retry", attempt=a, error=str(e)),
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

    def _needs_language_switch(self, language: str) -> bool:
        return bool(language) and language != self._language

    async def ensure_language(self, language: str) -> None:
        """Switch the TTS language to match the caller mid-call (reconnects only
        when it actually changes — Bulbul's language is fixed per config)."""
        if not self._needs_language_switch(language):
            return
        logger.info("tts.language_switch", frm=self._language, to=language)
        await self.connect(language=language, voice=self._voice, sample_rate=self._sample_rate)

    async def set_voice(self, voice: str) -> None:
        """Change the speaker for subsequent utterances (reconnects on change)."""
        if voice == self._voice:
            return
        if voice not in KNOWN_VOICES:
            logger.warning("tts.unknown_voice", voice=voice)
        await self.connect(language=self._language, voice=voice, sample_rate=self._sample_rate)

    async def send_text(self, text: str) -> None:
        if not self.is_connected:
            logger.warning("tts.reconnecting")
            await self.connect(
                language=self._language, voice=self._voice, sample_rate=self._sample_rate)
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
        """Persistent receive loop — survives across multiple utterances.

        Each completed synthesis emits a single ``None`` sentinel onto the audio
        queue (one per ``flush``). The loop does NOT exit on idle: it keepalive-
        pings instead, so the connection can be reused for the whole call. This
        is the fix for the documented "receive loop ended after first response"
        reuse bug.
        """
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(self._ws.recv(), timeout=20.0)
                except asyncio.TimeoutError:
                    # Idle between utterances — keep the socket warm, don't quit.
                    try:
                        await self._ws.send(json.dumps({"type": "ping"}))
                    except Exception:
                        break
                    continue

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
                    # Any completion event ends the current utterance. Treat all
                    # event types as terminal so a single missed "final" label
                    # can't hang the consumer.
                    await self._audio_queue.put(None)

                elif msg_type == "error":
                    logger.error("tts.error", msg=msg.get("data", {}).get("message"))
                    await self._audio_queue.put(None)

        except websockets.ConnectionClosed:
            logger.info("tts.connection_closed")
        except Exception as e:
            logger.error("tts.receive_error", error=str(e))
        finally:
            # Unblock any consumer waiting on a permanently-closed connection.
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
