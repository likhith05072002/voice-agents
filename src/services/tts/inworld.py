"""Inworld Streaming TTS Client over chunked HTTP.

Drop-in alternative to :class:`SarvamTTSClient` — same contract the turn
engine relies on (``send_text`` → ``flush`` → ``get_audio`` until ``None``,
``abort`` on barge-in), so ``build_engine`` can take either client.

Used for cloned voices (English/Hindi): each utterance is one POST to
``/tts/v1/voice:stream`` which returns newline-delimited JSON chunks of
base64 LINEAR16 audio at the requested sample rate. EVERY chunk is a
self-contained WAV (44B RIFF header + PCM) — each header is stripped here,
the engine wants raw contiguous PCM16.
"""

import asyncio
import base64
import json
import time

import httpx
import structlog

logger = structlog.get_logger()

STREAM_URL = "https://api.inworld.ai/tts/v1/voice:stream"


def _strip_wav_header(chunk: bytes) -> bytes:
    """Drop the RIFF/WAVE prelude Inworld puts on EVERY stream chunk.
    Scans for the 'data' sub-chunk instead of assuming 44 bytes, so extra
    header chunks (LIST/fact) can't leak header bytes into the audio."""
    if not chunk.startswith(b"RIFF"):
        return chunk
    i = chunk.find(b"data", 12, 256)
    if i < 0:
        return chunk
    return chunk[i + 8:]                 # skip 'data' id + 4-byte size


class InworldTTSClient:
    """Streaming TTS via Inworld's chunked-HTTP endpoint (PCM16 out)."""

    def __init__(self, api_key: str, model: str = "inworld-tts-1.5-mini",
                 pace: float = 1.0):
        self.api_key = api_key
        self.model = model
        # Inworld's speakingRate range is 0.5-1.5 (narrower than Sarvam's 2.0).
        self.pace = min(1.5, max(0.5, pace or 1.0))
        self._client: httpx.AsyncClient | None = None
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._language = "en-IN"
        self._voice = ""
        self._sample_rate = 16000
        self._send_time: float | None = None
        self._first_audio_time: float | None = None

    async def connect(
        self,
        language: str = "en-IN",
        voice: str = "",
        sample_rate: str = "16000",
    ) -> None:
        self._language = language
        self._voice = voice
        self._sample_rate = int(sample_rate)
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"Authorization": f"Basic {self.api_key}",
                         "Content-Type": "application/json"},
                timeout=httpx.Timeout(10.0, read=30.0),
            )
        self._first_audio_time = None
        logger.info("tts.inworld.connected", voice=voice, model=self.model,
                    sample_rate=self._sample_rate)

    async def ensure_language(self, language: str) -> None:
        """Inworld infers language from the text/script itself (verified live:
        an EN-cloned voice speaks Devanagari Hindi with no config change), so a
        caller-language switch is just bookkeeping here."""
        self._language = language

    async def set_voice(self, voice: str) -> None:
        self._voice = voice

    async def send_text(self, text: str) -> None:
        if self._client is None:
            await self.connect(self._language, self._voice,
                               str(self._sample_rate))
        self._send_time = time.perf_counter()
        # Chain onto any still-running synthesis so chunks can't interleave —
        # each task drains its predecessor before touching the queue.
        prev, self._task = self._task, None
        self._task = asyncio.create_task(self._synthesize(text, prev))

    async def flush(self) -> None:
        """No-op: each ``send_text`` is a complete utterance request; the
        stream's end (not an explicit flush) enqueues the ``None`` sentinel."""

    async def get_audio(self) -> bytes | None:
        """Get next audio chunk. Returns None when synthesis is complete."""
        return await self._audio_queue.get()

    async def _synthesize(self, text: str, prev: asyncio.Task | None) -> None:
        if prev is not None:
            try:
                await prev
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        first = True
        try:
            body = {
                "text": text,
                "voice_id": self._voice,
                "model_id": self.model,
                "audio_config": {
                    "audio_encoding": "LINEAR16",
                    "sample_rate_hertz": self._sample_rate,
                },
            }
            if abs(self.pace - 1.0) > 1e-3:
                body["audio_config"]["speaking_rate"] = self.pace
            async with self._client.stream("POST", STREAM_URL, json=body) as r:
                if r.status_code != 200:
                    detail = (await r.aread())[:300]
                    logger.error("tts.inworld.http", status=r.status_code,
                                 detail=detail.decode("utf-8", "replace"))
                    return
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    audio_b64 = (msg.get("result") or {}).get("audioContent")
                    if not audio_b64:
                        continue
                    # EVERY stream chunk is a self-contained WAV (44B RIFF
                    # header + PCM) — proven live: 21/21 chunks led with RIFF,
                    # and first-chunk-only stripping played each header as
                    # audio, an audible click every 0.5s ("tap tap tap").
                    chunk = _strip_wav_header(base64.b64decode(audio_b64))
                    if first:
                        first = False
                        if self._first_audio_time is None:
                            self._first_audio_time = time.perf_counter()
                            if self._send_time:
                                ttfa = (self._first_audio_time - self._send_time) * 1000
                                logger.info("tts.first_audio", ttfa_ms=round(ttfa))
                    if chunk:
                        await self._audio_queue.put(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.error("tts.inworld.error", error=str(e))
        finally:
            # put_nowait: safe on an unbounded queue AND safe inside a
            # cancelled task's finally (no await to re-raise into).
            self._audio_queue.put_nowait(None)

    async def abort(self) -> None:
        """Hard-stop in-flight synthesis after a barge-in. Same ordering
        discipline as the Sarvam client: await the dying task FIRST so its
        finally-sentinel lands on the OLD queue, then swap in a fresh queue
        that nothing from the interrupted turn can ever reach."""
        if self._task:
            task, self._task = self._task, None
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._audio_queue = asyncio.Queue()
        logger.info("tts.aborted")

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        if self._client:
            client, self._client = self._client, None
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass
        logger.info("tts.inworld.closed")

    async def reset(self) -> None:
        """Reset for next utterance without reconnecting."""
        self._first_audio_time = None
        self._send_time = None
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    @property
    def is_connected(self) -> bool:
        return self._client is not None
