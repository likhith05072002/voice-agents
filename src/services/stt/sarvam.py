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

from src.util.backoff import retry_async

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

    def __init__(self, api_key: str, model: str = "saaras:v3", buffer_ms: int = 100,
                 high_vad_sensitivity: bool = True):
        self.api_key = api_key
        self.model = model
        self.high_vad_sensitivity = high_vad_sensitivity
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
        self._seen_unhandled: set[str] = set()

    async def connect(self, language: str = "te-IN", sample_rate: int = 16000) -> None:
        self._language = language
        self._sample_rate = str(sample_rate)
        self._config_sent = False
        self._audio_buffer = b""

        async def _open():
            return await websockets.connect(
                "wss://api.sarvam.ai/speech-to-text/ws",
                additional_headers={"api-subscription-key": self.api_key},
                ping_interval=20,
                ping_timeout=10,
            )

        # Retry transient connect failures (DNS/TLS/cold LB) rather than failing
        # the call on the first hiccup.
        self._ws = await retry_async(
            _open, attempts=3, base_delay=0.2,
            on_retry=lambda a, e: logger.warning("stt.connect_retry", attempt=a, error=str(e)),
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
                    "high_vad_sensitivity": self.high_vad_sensitivity,
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

    def inject_vad(self, is_speech_start: bool) -> None:
        """Feed a locally-detected VAD event into the event stream. Live calls
        showed Sarvam sends no START/END_SPEECH signals, so the engine's
        instant-pause barge-in path never fired; a local energy detector in the
        audio path injects these instead."""
        self._transcript_queue.put_nowait(VADEvent(
            is_speech_start=is_speech_start, timestamp=time.perf_counter()))

    async def get_event(self) -> TranscriptEvent | VADEvent | None:
        return await self._transcript_queue.get()

    async def _handle_message(self, msg: dict) -> None:
        """Translate one Sarvam WS message into queue events.

        Unknown message types / signal shapes are LOGGED (once per type), never
        silently dropped — a live call showed zero VAD events arriving, and
        without this we cannot see what the server actually sent."""
        msg_type = msg.get("type")

        if msg_type == "data":
            data = msg.get("data", {})
            evt = TranscriptEvent(
                text=data.get("transcript", ""),
                is_final=True,
                language=data.get("language_code", ""),
                timestamp=time.perf_counter(),
            )
            if evt.text.strip():
                await self._transcript_queue.put(evt)

        elif msg_type == "events":
            data = msg.get("data", {})
            signal = data.get("signal_type", "")
            if signal in ("START_SPEECH", "END_SPEECH"):
                await self._transcript_queue.put(VADEvent(
                    is_speech_start=(signal == "START_SPEECH"),
                    timestamp=time.perf_counter(),
                ))
            else:
                self._log_unhandled(f"events/{signal}", msg)

        elif msg_type == "error":
            logger.error("stt.error", msg=msg.get("data", {}).get("message", ""))

        else:
            self._log_unhandled(str(msg_type), msg)

    def _log_unhandled(self, kind: str, msg: dict) -> None:
        if kind not in self._seen_unhandled:
            self._seen_unhandled.add(kind)
            logger.warning("stt.unhandled_message", kind=kind,
                           sample=str(msg)[:200].encode("ascii", "replace").decode())

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

                await self._handle_message(json.loads(raw))

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
