"""ElevenLabs Streaming TTS Client over chunked HTTP.

Third TTS provider behind the same engine contract as SarvamTTSClient and
InworldTTSClient (``send_text`` → ``flush`` → ``get_audio`` until ``None``,
``abort`` on barge-in). Used by the Cocolevio demo's provider dropdown.

Each utterance is one POST to ``/v1/text-to-speech/{voice_id}/stream`` with
``output_format=pcm_16000`` — the response body is RAW PCM16 mono 16k bytes
(no WAV header, no JSON framing; verified live), so chunks go straight onto
the audio queue. Free-tier accounts can only use their own/premade voices:
LIBRARY voice ids return 402 payment_required until the plan is paid.
"""

import asyncio
import time

import httpx
import structlog

logger = structlog.get_logger()

_BASE = "https://api.elevenlabs.io/v1/text-to-speech"


class ElevenLabsTTSClient:
    """Streaming TTS via ElevenLabs' chunked-HTTP endpoint (PCM16 out)."""

    def __init__(self, api_key: str, model: str = "eleven_flash_v2_5",
                 pace: float = 1.0):
        self.api_key = api_key
        self.model = model
        # ElevenLabs voice_settings.speed range is 0.7-1.2.
        self.pace = min(1.2, max(0.7, pace or 1.0))
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
                headers={"xi-api-key": self.api_key,
                         "Content-Type": "application/json"},
                timeout=httpx.Timeout(10.0, read=30.0),
            )
        self._first_audio_time = None
        logger.info("tts.elevenlabs.connected", voice=voice, model=self.model,
                    sample_rate=self._sample_rate)

    async def ensure_language(self, language: str) -> None:
        """Multilingual models read the language from the text itself."""
        self._language = language

    async def set_voice(self, voice: str) -> None:
        self._voice = voice

    async def send_text(self, text: str) -> None:
        if self._client is None:
            await self.connect(self._language, self._voice,
                               str(self._sample_rate))
        self._send_time = time.perf_counter()
        prev, self._task = self._task, None
        self._task = asyncio.create_task(self._synthesize(text, prev))

    async def flush(self) -> None:
        """No-op: each ``send_text`` is a complete utterance request."""

    async def get_audio(self) -> bytes | None:
        """Get next audio chunk. Returns None when synthesis is complete."""
        return await self._audio_queue.get()

    async def _synthesize(self, text: str, prev: asyncio.Task | None) -> None:
        if prev is not None:
            try:
                await prev
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        try:
            fmt = "pcm_16000" if self._sample_rate == 16000 else "pcm_8000"
            body: dict = {"text": text, "model_id": self.model}
            if abs(self.pace - 1.0) > 1e-3:
                body["voice_settings"] = {"speed": self.pace}
            async with self._client.stream(
                "POST", f"{_BASE}/{self._voice}/stream?output_format={fmt}",
                json=body,
            ) as r:
                if r.status_code != 200:
                    detail = (await r.aread())[:300]
                    logger.error("tts.elevenlabs.http", status=r.status_code,
                                 detail=detail.decode("utf-8", "replace"))
                    return
                first = True
                async for chunk in r.aiter_bytes():
                    if not chunk:
                        continue
                    if first:
                        first = False
                        if self._first_audio_time is None:
                            self._first_audio_time = time.perf_counter()
                            if self._send_time:
                                ttfa = (self._first_audio_time - self._send_time) * 1000
                                logger.info("tts.first_audio", ttfa_ms=round(ttfa))
                    await self._audio_queue.put(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.error("tts.elevenlabs.error", error=str(e))
        finally:
            self._audio_queue.put_nowait(None)

    async def abort(self) -> None:
        """Same ordering discipline as the other clients: await the dying
        task FIRST so its sentinel lands on the OLD queue, then swap fresh."""
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
        logger.info("tts.elevenlabs.closed")

    async def reset(self) -> None:
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
