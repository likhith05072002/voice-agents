"""Latency regression tests — pipeline stages must overlap, not serialize."""

import asyncio
import time

from src.pipeline.turn_engine import TurnEngine
from src.services.stt.sarvam import TranscriptEvent
from src.services.llm.sarvam import SentenceEvent


class _STT:
    def __init__(self): self.q = asyncio.Queue()
    async def get_event(self): return await self.q.get()


class _SlowLLM:
    """First token after 100ms (models LLM TTFT). Replies in the caller's
    language (Hindi) so the language guard doesn't trigger a regenerate."""
    async def generate_sentences(self, messages, queue):
        await asyncio.sleep(0.1)
        await queue.put(SentenceEvent(text="नमस्ते जी. ", is_first=True, timestamp=0.0))
        await queue.put(None)
        return "नमस्ते जी. "
    def cancel(self): ...


class _SlowLangTTS:
    """A language switch costs 100ms (models a TTS reconnect)."""
    def __init__(self): self._p = []
    async def ensure_language(self, lang): await asyncio.sleep(0.1)
    async def reset(self): self._p = []
    async def send_text(self, t): self._p = [b"\x01\x00" * 160, None]
    async def flush(self): ...
    async def get_audio(self): return self._p.pop(0) if self._p else None


async def test_language_switch_overlaps_llm_generation():
    first = []

    async def send_media(frame):
        if not first:
            first.append(time.perf_counter())

    engine = TurnEngine(stt=_STT(), llm=_SlowLLM(), tts=_SlowLangTTS(),
                        send_media=send_media, system_prompt="s", greeting_text="",
                        frame_pace_s=0, enable_language_switch=True)
    run = asyncio.create_task(engine.run())
    t0 = time.perf_counter()
    await engine.stt.q.put(TranscriptEvent(text="namaste", is_final=True,
                                           language="hi-IN", timestamp=0.0))
    while not first:
        await asyncio.sleep(0.005)
    elapsed = first[0] - t0
    await engine.stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)

    # Overlapped reconnect + TTFT ≈ max(0.1, 0.1) = ~0.1s.
    # Serial (the old order) would be ~0.2s. Assert clearly under the serial cost.
    assert elapsed < 0.17, f"first audio took {elapsed:.3f}s — stages not overlapping"
