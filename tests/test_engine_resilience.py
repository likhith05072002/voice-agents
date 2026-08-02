"""One bad utterance must never end the call.

Live symptom this guards: mid-call the agent stopped answering and the log
showed `barge_in.confirmed` with no following `turn.start`. The interrupt
path raised, the exception killed the engine's event loop, and the caller's
teardown swallowed it — so the agent went mute for the rest of the call with
nothing in the journal to explain it.
"""

import asyncio

from src.pipeline.turn_engine import TurnEngine
from src.services.stt.sarvam import TranscriptEvent


class _STT:
    def __init__(self): self.q = asyncio.Queue()
    async def get_event(self): return await self.q.get()
    async def flush(self): ...


class _TTS:
    def __init__(self):
        self.spoken: list[str] = []
        self._q: list = []

    async def reset(self): ...
    async def send_text(self, t):
        self.spoken.append(t)
        # Several chunks, delivered SLOWLY, so the turn is genuinely still
        # SPEAKING when the next utterance lands — otherwise the fake pipeline
        # finishes instantly and the interrupt path is never exercised.
        self._q.extend([b"\x01\x00" * 160] * 8 + [None])
    async def flush(self): ...
    async def get_audio(self):
        await asyncio.sleep(0.12)
        return self._q.pop(0) if self._q else None
    async def abort(self): ...      # already guarded in _confirm_interrupt


class _LLM:
    """cancel() is called by the interrupt path and is NOT individually
    guarded — a raise there escapes _confirm_interrupt entirely, which is the
    shape of crash that silently killed the engine loop."""
    def __init__(self, explode_on_cancel=False):
        self.calls = 0
        self.explode = explode_on_cancel
        self._exploded = False

    async def generate_sentences(self, messages, queue):
        self.calls += 1
        from src.services.llm.sarvam import SentenceEvent
        await queue.put(SentenceEvent(text="Sure, here you go. ", is_first=True,
                                      timestamp=0.0))
        await queue.put(None)
        return "Sure, here you go. "

    def cancel(self):
        if self.explode and not self._exploded:
            self._exploded = True        # blow up once, mid-interrupt
            raise RuntimeError("llm.cancel failed during interrupt")


async def _drive(explode: bool) -> tuple[_TTS, _LLM]:
    stt, tts, llm = _STT(), _TTS(), _LLM(explode_on_cancel=explode)

    async def send_media(frame): ...

    engine = TurnEngine(stt=stt, llm=llm, tts=tts, send_media=send_media,
                        system_prompt="s", greeting_text="", frame_pace_s=0)
    run = asyncio.create_task(engine.run())

    # First utterance -> a normal turn (agent starts speaking).
    await stt.q.put(TranscriptEvent(text="tell me about your services",
                                    is_final=True, language="en-IN", timestamp=0.0))
    await asyncio.sleep(0.3)
    # Second utterance WHILE speaking -> the interrupt path (abort() raises).
    await stt.q.put(TranscriptEvent(text="wait stop I have another question",
                                    is_final=True, language="en-IN", timestamp=0.0))
    await asyncio.sleep(0.5)
    # Third utterance: does the engine still answer, or did it die?
    await stt.q.put(TranscriptEvent(text="what are your opening hours",
                                    is_final=True, language="en-IN", timestamp=0.0))
    await asyncio.sleep(0.6)

    alive = not run.done()
    await stt.q.put(None)
    run.cancel()
    try:
        await run
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    assert alive, "engine task died — the call would have gone mute"
    return tts, llm


async def test_call_survives_a_crashing_interrupt():
    tts, llm = await _drive(explode=True)
    # The engine kept going and answered AFTER the failed interrupt.
    assert llm.calls >= 2, f"engine stopped generating (calls={llm.calls})"
    assert tts.spoken, "nothing was ever spoken"


async def test_healthy_interrupt_still_works():
    tts, llm = await _drive(explode=False)
    assert llm.calls >= 2 and tts.spoken
