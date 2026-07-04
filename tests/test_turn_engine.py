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
        # Append (like the real client's queue): with pipelined synthesis the
        # engine sends sentence N+1 before consuming sentence N's audio.
        self._pending += [b"\x01\x00" * (size // 2), None]

    async def flush(self):
        pass

    async def get_audio(self):
        while not self._pending:
            await asyncio.sleep(0.005)      # audio "arrives" asynchronously
        return self._pending.pop(0)


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


async def test_no_false_recovery_while_caller_still_speaking():
    """A long interruption outlives false_timeout; the agent must NOT resume
    talking over the caller (the mid-phrase stutter heard on real calls)."""
    stt = FakeSTT()
    llm = FakeLLM(["A long answer that keeps going. "])
    tts = FakeTTS([64000])
    sent = []
    engine = _engine(stt, llm, tts, sent, frame_pace_s=0.005,
                     false_timeout_s=0.05, speech_end_grace_s=0.05)

    run = asyncio.create_task(engine.run())
    await stt.q.put(TranscriptEvent(text="hi", is_final=True, language="en", timestamp=0.0))
    assert await _wait_until(lambda: engine.state == State.SPEAKING)

    # Caller starts talking and KEEPS talking (no END_SPEECH yet).
    await stt.q.put(VADEvent(is_speech_start=True, timestamp=0.0))
    assert await _wait_until(lambda: engine._candidate is True)
    await asyncio.sleep(0.2)                     # several false_timeouts elapse
    assert engine._candidate is True             # still paused — no stutter

    # Caller stops -> END_SPEECH -> grace resumes as before.
    await stt.q.put(VADEvent(is_speech_start=False, timestamp=0.0))
    assert await _wait_until(lambda: engine._candidate is False, timeout=1.0)
    assert engine.state == State.SPEAKING

    await stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)


async def test_speech_end_triggers_fast_recovery():
    """A VAD blip (start then end, no transcript) must resume via the short
    grace, NOT wait the full false_timeout. We set false_timeout absurdly long
    so only the grace path can resume in time."""
    stt = FakeSTT()
    llm = FakeLLM(["A long answer that keeps going. "])
    tts = FakeTTS([64000])
    sent = []
    engine = _engine(stt, llm, tts, sent, frame_pace_s=0.005,
                     false_timeout_s=5.0, speech_end_grace_s=0.05)

    run = asyncio.create_task(engine.run())
    await stt.q.put(TranscriptEvent(text="hi", is_final=True, language="en", timestamp=0.0))
    assert await _wait_until(lambda: engine.state == State.SPEAKING)

    await stt.q.put(VADEvent(is_speech_start=True, timestamp=0.0))
    assert await _wait_until(lambda: engine._candidate is True)
    await stt.q.put(VADEvent(is_speech_start=False, timestamp=0.0))   # blip ended
    # Resumes within the 50ms grace, far below the 5s false_timeout.
    assert await _wait_until(lambda: engine._candidate is False, timeout=1.0)
    assert engine.state == State.SPEAKING

    await stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)


async def test_real_interrupt_still_confirms_after_speech_end():
    """Even with the fast-recovery path armed, a genuine transcript that lands
    after END_SPEECH must still interrupt and truncate to what played."""
    stt = FakeSTT()
    llm = FakeLLM(["First. ", "Second sentence long. "])
    tts = FakeTTS([320, 64000])
    sent = []
    # grace == false_timeout so neither timer pre-resumes; transcript decides.
    engine = _engine(stt, llm, tts, sent, frame_pace_s=0.005,
                     false_timeout_s=5.0, speech_end_grace_s=5.0)

    run = asyncio.create_task(engine.run())
    await stt.q.put(TranscriptEvent(text="hi", is_final=True, language="en", timestamp=0.0))
    assert await _wait_until(lambda: engine._spoken == "First. ")

    await stt.q.put(VADEvent(is_speech_start=True, timestamp=0.0))
    assert await _wait_until(lambda: engine._candidate is True)
    await stt.q.put(VADEvent(is_speech_start=False, timestamp=0.0))
    await stt.q.put(TranscriptEvent(text="what about silver price",
                                    is_final=True, language="en", timestamp=0.0))

    assert await _wait_until(
        lambda: any(h["role"] == "assistant" for h in engine.history))
    assert engine.history[1] == {"role": "assistant", "content": "First. "}

    await stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)


async def test_smart_endpointing_merges_fragment_then_fires():
    """A fragment ('...price for') is held, then merged with the continuation
    ('twenty two carat gold') and the turn fires on the complete utterance."""
    stt = FakeSTT()
    llm = FakeLLM(["Sure. "])
    tts = FakeTTS([320])
    sent = []
    engine = _engine(stt, llm, tts, sent, frame_pace_s=0,
                     enable_smart_endpointing=True, continuation_timeout_s=2.0)

    run = asyncio.create_task(engine.run())
    await stt.q.put(TranscriptEvent(text="what is the price for",
                                    is_final=True, language="en", timestamp=0.0))
    # Held, not fired: no user turn yet.
    assert await _wait_until(lambda: engine._pending_user_text != "")
    assert not any(h["role"] == "user" for h in engine.history)
    # Continuation completes it (ends on a content word, not a conjunction).
    await stt.q.put(TranscriptEvent(text="twenty two carat gold",
                                    is_final=True, language="en", timestamp=0.0))
    assert await _wait_until(
        lambda: any(h["role"] == "user" for h in engine.history))
    user = next(h for h in engine.history if h["role"] == "user")
    assert user["content"] == "what is the price for twenty two carat gold"

    await stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)


async def test_smart_endpointing_fires_on_timeout_when_no_continuation():
    """If the caller never continues, the buffered fragment still fires after
    the continuation timeout — input is never dropped."""
    stt = FakeSTT()
    llm = FakeLLM(["Ok. "])
    tts = FakeTTS([320])
    sent = []
    engine = _engine(stt, llm, tts, sent, frame_pace_s=0,
                     enable_smart_endpointing=True, continuation_timeout_s=0.05)

    run = asyncio.create_task(engine.run())
    await stt.q.put(TranscriptEvent(text="I want to know about",
                                    is_final=True, language="en", timestamp=0.0))
    # Fires the fragment after the 50ms timeout even with no continuation.
    assert await _wait_until(
        lambda: any(h["role"] == "user" for h in engine.history), timeout=1.0)
    user = next(h for h in engine.history if h["role"] == "user")
    assert user["content"] == "I want to know about"

    await stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)


async def test_idle_reprompts_then_hangs_up():
    """A silent caller is re-prompted, then the call ends (engine.run returns)."""
    stt, llm, tts, sent = FakeSTT(), FakeLLM(["x. "]), FakeTTS([160]), []
    engine = _engine(stt, llm, tts, sent, frame_pace_s=0,
                     enable_idle=True, idle_reprompt_s=0.05, idle_hangup_s=0.25,
                     reprompt_text="still there?")
    run = asyncio.create_task(engine.run())
    # No events ever -> reprompt fires, then idle hangup ends the loop.
    await asyncio.wait_for(run, timeout=3.0)
    assert engine._reprompted is True
    assert len(sent) > 0          # the re-prompt audio was actually played


async def test_idle_disabled_does_not_hang_up():
    stt, llm, tts, sent = FakeSTT(), FakeLLM(["x. "]), FakeTTS([160]), []
    engine = _engine(stt, llm, tts, sent, frame_pace_s=0, enable_idle=False)
    run = asyncio.create_task(engine.run())
    # With idle off, run blocks until we close the stream.
    assert not await _wait_until(lambda: run.done(), timeout=0.3)
    await stt.q.put(None)
    await asyncio.wait_for(run, timeout=2.0)


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


async def test_confirmed_interrupt_aborts_tts_socket():
    """A confirmed barge-in must hard-abort TTS so no stale audio from the
    interrupted answer can pair with the next answer's text."""
    import asyncio
    from src.pipeline.turn_engine import TurnEngine, State
    from src.services.stt.sarvam import TranscriptEvent, VADEvent
    from src.services.llm.sarvam import SentenceEvent

    class _STT:
        def __init__(self): self.q = asyncio.Queue()
        async def get_event(self): return await self.q.get()

    class _LLM:
        async def generate_sentences(self, messages, queue):
            await queue.put(SentenceEvent(text="A long answer sentence. ",
                                          is_first=True, timestamp=0.0))
            await asyncio.sleep(0.2)          # keep the turn alive to interrupt
            await queue.put(None)
            return "A long answer sentence. "
        def cancel(self): ...

    class _TTS:
        def __init__(self):
            self.aborted = 0
            self._p = []
        async def reset(self): self._p = []
        async def send_text(self, t): self._p = [b"\x01\x00" * 320, None]
        async def flush(self): ...
        async def get_audio(self):
            await asyncio.sleep(0.01)
            return self._p.pop(0) if self._p else None
        async def abort(self): self.aborted += 1

    sent = []
    async def _send(f): sent.append(f)
    tts = _TTS()
    engine = TurnEngine(stt=_STT(), llm=_LLM(), tts=tts, send_media=_send,
                        system_prompt="s", greeting_text="", frame_pace_s=0)
    run = asyncio.create_task(engine.run())
    await engine.stt.q.put(TranscriptEvent(text="tell me everything about gold",
                                           is_final=True, language="en-IN", timestamp=0.0))
    for _ in range(200):
        if engine.state == State.SPEAKING:
            break
        await asyncio.sleep(0.005)
    # caller interrupts with a real question while the agent speaks
    await engine.stt.q.put(TranscriptEvent(text="wait where are you located",
                                           is_final=True, language="en-IN", timestamp=0.0))
    for _ in range(300):
        if tts.aborted:
            break
        await asyncio.sleep(0.005)
    await engine.stt.q.put(None)
    try:
        await asyncio.wait_for(run, timeout=4.0)
    except asyncio.TimeoutError:
        run.cancel()
    assert tts.aborted >= 1


def test_greeting_only_openers_detected():
    from src.pipeline.turn_engine import _is_greeting_only
    assert _is_greeting_only("ನಮಸ್ಕಾರ.") is True
    assert _is_greeting_only("नमस्ते।") is True
    assert _is_greeting_only("Hello!") is True
    assert _is_greeting_only("ನಮಸ್ಕಾರ, ನಮ್ಮ ಕಂಪನಿ ಆಸ್ಟಿನ್‌ನಲ್ಲಿದೆ.") is False
    assert _is_greeting_only("We were founded in 2015.") is False
    assert _is_greeting_only("") is False


def test_long_call_junk_gates():
    """Backchannels, self-echo, and language blips must not pollute history."""
    import asyncio
    from src.pipeline.turn_engine import TurnEngine

    class _N:  # bare fakes; we only exercise the pure helpers
        pass

    e = TurnEngine.__new__(TurnEngine)
    e.history = [{"role": "assistant",
                  "content": "We offer cloud migration and custom software development services."}]
    e._caller_language = "kn-IN"
    e._lang_candidate = ""
    # backchannel-only finals
    assert e._is_backchannel_only("Hmm") is True
    assert e._is_backchannel_only("ok") is True
    assert e._is_backchannel_only("ಸರಿ") is True
    assert e._is_backchannel_only("ok tell me the price") is False
    # self-echo: user final made of the agent's own words
    assert e._looks_like_own_echo("cloud migration and custom software development") is True
    assert e._looks_like_own_echo("what is the gold price today") is False
    # sticky language: one-word blip doesn't flip; 3+ words or 2 sightings do
    e._track_language("te-IN", "సరే")
    assert e._caller_language == "kn-IN"
    e._track_language("hi-IN", "अब हम हिंदी में बात करते हैं")
    assert e._caller_language == "hi-IN"
    e._track_language("en-IN", "ok")
    assert e._caller_language == "hi-IN"
    e._track_language("en-IN", "ok")          # second consecutive sighting
    assert e._caller_language == "en-IN"
