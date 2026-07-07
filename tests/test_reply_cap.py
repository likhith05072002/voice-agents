"""max_reply_sentences: the deterministic backstop for models that ignore
"keep it short" prompt rules (heard live: a five-sentence identity ramble
from the demo agent). Capped turns speak exactly the cap; uncapped agents
are unchanged."""

import asyncio

from src.pipeline.turn_engine import TurnEngine
from src.services.stt.sarvam import TranscriptEvent
from src.services.llm.sarvam import SentenceEvent


class _STT:
    def __init__(self): self.q = asyncio.Queue()
    async def get_event(self): return await self.q.get()


class _RambleLLM:
    """Ignores every brevity instruction: emits five sentences per turn."""
    def __init__(self): self.cancelled = False
    async def generate_sentences(self, messages, queue):
        for i in range(5):
            await queue.put(SentenceEvent(
                text=f"This is spoken sentence number {i} of the ramble. ",
                is_first=(i == 0), timestamp=0.0))
        await queue.put(None)
        return ""
    def cancel(self): self.cancelled = True


class _RecordingTTS:
    def __init__(self): self.spoken = []; self._q = []
    async def reset(self): ...
    async def send_text(self, t):
        self.spoken.append(t)
        self._q.extend([b"\x01\x00" * 160, None])
    async def flush(self): ...
    async def get_audio(self):
        return self._q.pop(0) if self._q else None


async def _run_turn(cap: int):
    tts, llm = _RecordingTTS(), _RambleLLM()

    async def send_media(frame): ...

    engine = TurnEngine(stt=_STT(), llm=llm, tts=tts, send_media=send_media,
                        system_prompt="s", greeting_text="", frame_pace_s=0,
                        max_reply_sentences=cap)
    run = asyncio.create_task(engine.run())
    await engine.stt.q.put(TranscriptEvent(text="who are you exactly",
                                           is_final=True, language="en-IN",
                                           timestamp=0.0))
    await asyncio.sleep(0.5)               # let the turn fully play out
    await engine.stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)
    return tts, llm


async def test_cap_stops_rambling_at_two_sentences():
    tts, llm = await _run_turn(cap=2)
    assert len(tts.spoken) == 2, f"spoke {len(tts.spoken)} sentences, cap was 2"
    assert llm.cancelled, "capped turn must cancel the still-streaming LLM"


async def test_uncapped_agents_are_unchanged():
    tts, _ = await _run_turn(cap=0)
    assert len(tts.spoken) == 5
