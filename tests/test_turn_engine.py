"""Behavioural tests for the TurnEngine guard stack.

Proves the properties the old design got wrong AND the A-grade guard behaviour:
  - a full turn completes without blocking the event loop
  - a real interruption cancels the turn and truncates history to what played
  - a *backchannel* ("uh-huh") does NOT interrupt — playback resumes
  - a false interruption (VAD, no transcript) recovers after the timeout
  - a hard phrase ("stop") interrupts immediately
"""

import asyncio

from src.pipeline.turn_engine import TurnEngine, State
from src.services.stt.sarvam import TranscriptEvent, VADEvent
from src.services.llm.sarvam import SentenceEvent


class FakeSTT:
    def __init__(self):
        self.q: asyncio.Queue = asyncio.Queue()

    async def get_event(self):
        return await self.q.get()


class FakeLLM:
    def __init__(self, sentences):
        self.sentences = sentences
        self.cancelled = False

    async def generate_sentences(self, messages, queue):
        self.cancelled = False
        for s in self.sentences:
            if self.cancelled:
                break
            await queue.put(SentenceEvent(text=s, is_first=False, timestamp=0.0))
        await queue.put(None)
        return "".join(self.sentences)

    def cancel(self):
        self.cancelled = True


class FakeTTS:
    """Per-utterance chunk sizes drive how long each sentence takes to play.

    Once the scripted sizes are exhausted, falls back to a tiny chunk so any
    follow-on turn (e.g. the one started by a confirmed barge-in) drains fast.
    """

    def __init__(self, sizes, default=160):
        self._sizes = list(sizes)
        self._default = default
        self._i = 0
        self._pending = []

    async def reset(self):
        self._pending = []

    async def send_text(self, text):
        size = self._sizes[self._i] if self._i < len(self._sizes) else self._default
        self._i += 1
        self._pending = [b"\x01\x00" * (size // 2), None]

    async def flush(self):
        pass

    async def get_audio(self):
        return self._pending.pop(0) if self._pending else None


def _engine(stt, llm, tts, sent, **kw):
    async def send_media(frame):
        sent.append(frame)
    return TurnEngine(
        stt=stt, llm=llm, tts=tts, send_media=send_media,
        system_prompt="sys", greeting_text="", **kw,
    )


async def _wait_until(predicate, timeout=2.0):
    """Poll a condition instead of sleeping a fixed time (deterministic)."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return False


async def test_full_turn_completes_and_records_history():
    stt, llm, tts, sent = FakeSTT(), FakeLLM(["Hello there. "]), FakeTTS([320]), []
    engine = _engine(stt, llm, tts, sent, frame_pace_s=0)

    run = asyncio.create_task(engine.run())
    await stt.q.put(TranscriptEvent(text="hi", is_final=True, language="en", timestamp=0.0))
    await stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)

    assert engine.history == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Hello there. "},
    ]
    assert len(sent) > 0


async def test_real_barge_in_cancels_and_truncates_to_played():
    # sentence 1 tiny (plays instantly), sentence 2 huge (still playing on barge-in)
    stt = FakeSTT()
    llm = FakeLLM(["First sentence. ", "Second sentence. "])
    tts = FakeTTS([320, 64000])
    sent = []
    engine = _engine(stt, llm, tts, sent, frame_pace_s=0.005)

    run = asyncio.create_task(engine.run())
    await stt.q.put(TranscriptEvent(text="hi", is_final=True, language="en", timestamp=0.0))
    # Wait until sentence 1 has actually played (deterministic precondition).
    assert await _wait_until(lambda: engine._spoken == "First sentence. ")
    assert engine.state == State.SPEAKING

    # Caller barges in: VAD onset, then a genuine interruption transcript.
    await stt.q.put(VADEvent(is_speech_start=True, timestamp=0.0))
    assert await _wait_until(lambda: engine._candidate is True)
    await stt.q.put(TranscriptEvent(text="what about silver price",
                                    is_final=True, language="en", timestamp=0.0))
    assert await _wait_until(
        lambda: any(h["role"] == "assistant" for h in engine.history))

    # History truncated to ONLY the sentence that actually played.
    assert engine.history[0] == {"role": "user", "content": "hi"}
    assert engine.history[1] == {"role": "assistant", "content": "First sentence. "}
    # Sentence 2 never fully played -> never recorded as heard.
    assert not any("Second sentence" in h["content"]
                   for h in engine.history if h["role"] == "assistant")

    await stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)


async def test_backchannel_does_not_interrupt():
    stt = FakeSTT()
    llm = FakeLLM(["A long answer that keeps going. "])
    tts = FakeTTS([64000])  # long playback
    sent = []
    engine = _engine(stt, llm, tts, sent, frame_pace_s=0.005)

    run = asyncio.create_task(engine.run())
    await stt.q.put(TranscriptEvent(text="hi", is_final=True, language="en", timestamp=0.0))
    assert await _wait_until(lambda: engine.state == State.SPEAKING)
    turn = engine._current_turn

    # Caller says "uh-huh" mid-answer: VAD pauses, transcript clears it.
    await stt.q.put(VADEvent(is_speech_start=True, timestamp=0.0))
    assert await _wait_until(lambda: engine._candidate is True)  # paused, judging
    await stt.q.put(TranscriptEvent(text="uh-huh", is_final=True, language="en", timestamp=0.0))
    assert await _wait_until(lambda: engine._candidate is False)

    # NOT interrupted: still the same turn, still speaking, no truncated history.
    assert engine.state == State.SPEAKING
    assert turn is engine._current_turn and not turn.done()
    assert not any(h["role"] == "assistant" for h in engine.history)

    await stt.q.put(None)
    await asyncio.wait_for(run, timeout=3.0)


async def test_false_interruption_recovers_after_timeout():
    stt = FakeSTT()
    llm = FakeLLM(["Another long answer here. "])
    tts = FakeTTS([64000])
    sent = []
    engine = _engine(stt, llm, tts, sent, frame_pace_s=0.005, false_timeout_s=0.05)

    run = asyncio.create_task(engine.run())
    await stt.q.put(TranscriptEvent(text="hi", is_final=True, language="en", timestamp=0.0))
    assert await _wait_until(lambda: engine.state == State.SPEAKING)

    # VAD fires (noise) but no transcript ever arrives.
    await stt.q.put(VADEvent(is_speech_start=True, timestamp=0.0))
    assert await _wait_until(lambda: engine._candidate is True)
    # exceeds false_timeout_s -> recovery resumes playback
    assert await _wait_until(lambda: engine._candidate is False)
    assert engine.state == State.SPEAKING

    await stt.q.put(None)
    await asyncio.wait_for(run, timeout=3.0)


async def test_hard_phrase_interrupts_immediately():
    stt = FakeSTT()
    llm = FakeLLM(["I will keep talking for a while. "])
    tts = FakeTTS([64000])
    sent = []
    engine = _engine(stt, llm, tts, sent, frame_pace_s=0.005)

    run = asyncio.create_task(engine.run())
    await stt.q.put(TranscriptEvent(text="hi", is_final=True, language="en", timestamp=0.0))
    assert await _wait_until(lambda: engine.state == State.SPEAKING)

    await stt.q.put(VADEvent(is_speech_start=True, timestamp=0.0))
    assert await _wait_until(lambda: engine._candidate is True)
    await stt.q.put(TranscriptEvent(text="stop", is_final=True, language="en", timestamp=0.0))

    # "stop" is a hard phrase -> interrupted even though it is a single word;
    # a new turn for "stop" is started.
    assert await _wait_until(
        lambda: any(h == {"role": "user", "content": "stop"} for h in engine.history))
    assert engine._candidate is False

    await stt.q.put(None)
    await asyncio.wait_for(run, timeout=3.0)
