"""Sarvam Saaras V3 Streaming STT Client over WebSocket.

Message format (from Pipecat source + API docs):
  First message: {"config": {...}, "audio": {"data": b64, "encoding": "audio/wav", "sample_rate": "16000"}}
  Subsequent:    {"audio": {"data": b64, "encoding": "audio/wav", "sample_rate": "16000"}}

Responses:
  {"type": "events", "data": {"signal_type": "START_SPEECH"|"END_SPEECH"}}
  {"type": "data", "data": {"transcript": "...", "language_code": "..."}}
"""

import asyncio
import base64
import json
import time
from dataclasses import dataclass

import structlog
import websockets

logger = structlog.get_logger()


@dataclass
class TranscriptEvent:
    text: str
    is_final: bool
    language: str
    timestamp: float


@dataclass
class VADEvent:
    is_speech_start: bool
    timestamp: float


class SarvamSTTClient:
    """Streaming Speech-to-Text via Sarvam Saaras V3 WebSocket."""

    def __init__(self, api_key: str, model: str = "saaras:v3", buffer_ms: int = 100):
        self.api_key = api_key
        self.model = model
        self._ws = None
        self._transcript_queue: asyncio.Queue[TranscriptEvent | VADEvent | None] = asyncio.Queue()
        self._receive_task = None
        self._config_sent = False
        self._language = "te-IN"
        self._sample_rate = "16000"
        self._audio_buffer = b""
        # Bytes to accumulate before sending: 16kHz * 2 bytes/sample * (ms/1000).
        # Smaller buffer -> faster VAD/barge-in signals, more websocket traffic.
        self._buffer_bytes = max(1, int(16000 * 2 * buffer_ms / 1000))

    async def connect(self, language: str = "te-IN", sample_rate: int = 16000) -> None:
        self._language = language
        self._sample_rate = str(sample_rate)
        self._config_sent = False
        self._audio_buffer = b""

        self._ws = await websockets.connect(
            "wss://api.sarvam.ai/speech-to-text/ws",
            additional_headers={"api-subscription-key": self.api_key},
            ping_interval=20,
            ping_timeout=10,
        )
        self._receive_task = asyncio.create_task(self._receive_loop())
        logger.info("stt.connected", language=language, model=self.model)

    async def send_audio(self, pcm_bytes: bytes, sample_rate: int = 16000) -> None:
        """Send PCM16 audio to STT. Buffers small chunks into ~500ms blocks for reliability."""
        if not self._ws:
            return

        self._audio_buffer += pcm_bytes

        # Buffer until we have ~buffer_ms of audio so Sarvam gets meaningful
        # chunks without delaying VAD/barge-in signals more than necessary.
        if len(self._audio_buffer) < self._buffer_bytes:
            return

        chunk = self._audio_buffer
        self._audio_buffer = b""

        b64 = base64.b64encode(chunk).decode("ascii")
        audio_data = {
            "data": b64,
            "encoding": "audio/wav",
            "sample_rate": self._sample_rate,
        }

        if not self._config_sent:
            msg = {
                "config": {
                    "model": self.model,
                    "language_code": self._language,
                    "sample_rate": self._sample_rate,
                    "vad_signals": True,
                    "high_vad_sensitivity": True,
                },
                "audio": audio_data,
            }
            self._config_sent = True
        else:
            msg = {"audio": audio_data}

        await self._ws.send(json.dumps(msg))
        if self._config_sent and not hasattr(self, '_logged_send'):
            self._logged_send = True
            logger.info("stt.audio_sent", chunk_bytes=len(chunk), msg_keys=list(msg.keys()))

    async def flush(self) -> None:
        """Send flush signal to get final transcript."""
        if self._ws:
            await self._ws.send(json.dumps({"type": "flush_signal"}))

    async def get_event(self) -> TranscriptEvent | VADEvent | None:
        return await self._transcript_queue.get()

    async def _receive_loop(self) -> None:
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(self._ws.recv(), timeout=60.0)
                except asyncio.TimeoutError:
                    # Send keepalive ping
                    try:
                        await self._ws.send(json.dumps({"type": "ping"}))
                    except Exception:
                        break
                    continue

                msg = json.loads(raw)
                msg_type = msg.get("type")

                if msg_type == "data":
                    data = msg["data"]
                    evt = TranscriptEvent(
                        text=data.get("transcript", ""),
                        is_final=True,
                        language=data.get("language_code", ""),
                        timestamp=time.perf_counter(),
                    )
                    if evt.text.strip():
                        await self._transcript_queue.put(evt)

                elif msg_type == "events":
                    data = msg["data"]
                    signal = data.get("signal_type", "")
                    if signal in ("START_SPEECH", "END_SPEECH"):
                        evt = VADEvent(
                            is_speech_start=(signal == "START_SPEECH"),
                            timestamp=time.perf_counter(),
                        )
                        await self._transcript_queue.put(evt)

                elif msg_type == "error":
                    logger.error("stt.error", msg=msg.get("data", {}).get("message", ""))

        except websockets.ConnectionClosed:
            logger.info("stt.connection_closed")
        except Exception as e:
            logger.error("stt.receive_error", error=str(e))
        finally:
            await self._transcript_queue.put(None)

    async def close(self) -> None:
        if self._receive_task:
            self._receive_task.cancel()
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("stt.closed")
