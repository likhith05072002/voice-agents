"""The 2026-08-03 Sarvam incident, distilled into regressions.

What actually happened: Sarvam's chat-completions gateway changed overnight —
``reasoning_effort: null`` (our documented low-latency setting) began hanging
the request forever instead of being accepted, and their backends flapped
(connection accepted, zero bytes ever sent). Every turn ended as 30s of dead
air with nothing in the log; TTS/STT kept working, which made it look like
our pipeline was broken.

Three defenses, each tested here:
  1. The payload NEVER contains ``reasoning_effort: null`` (omit instead).
  2. A stream that sends no bytes fails in seconds, not 30, and retries once
     — but never after anything was already spoken (no double-speak).
  3. A turn that produced nothing SAYS SO to the caller (ephemeral apology)
     and logs turn.failed, instead of ghosting them.
"""

import asyncio
import json

import pytest

from src.pipeline.turn_engine import TurnEngine, _failure_line
from src.services.llm.sarvam import SarvamLLMClient, SentenceEvent
from src.services.stt.sarvam import TranscriptEvent


# ─── 1. payload shape ───

def test_reasoning_effort_none_is_omitted_not_null():
    c = SarvamLLMClient(api_key="k", reasoning_effort=None)
    for body in (c._payload([]), c._complete_payload([])):
        assert "reasoning_effort" not in body, \
            "null hangs Sarvam's gateway since 2026-08-03 — omit the field"
        assert "null" not in json.dumps(body)


def test_reasoning_effort_string_still_passes_through():
    c = SarvamLLMClient(api_key="k", reasoning_effort="low")
    assert c._payload([])["reasoning_effort"] == "low"
    assert c._complete_payload([])["reasoning_effort"] == "low"


# ─── 2. dead-backend watchdog + single safe retry ───

class _HangingStream:
    """Accepts the request, never sends a byte — today's failure mode."""
    status_code = 200

    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        self.log.append("attempt")
        return self

    async def __aexit__(self, *a):
        return False

    def aiter_lines(self):
        async def gen():
            await asyncio.Event().wait()      # never yields, never ends
            yield ""                          # pragma: no cover
        return gen()


class _FakeHTTP:
    def __init__(self, log):
        self.log = log

    def stream(self, *a, **k):
        return _HangingStream(self.log)


@pytest.mark.asyncio
async def test_zero_byte_stream_fails_fast_and_retries_once(monkeypatch):
    c = SarvamLLMClient(api_key="k")
    monkeypatch.setattr(SarvamLLMClient, "FIRST_LINE_TIMEOUT_S", 0.05)
    log: list = []
    c._client = _FakeHTTP(log)
    q: asyncio.Queue = asyncio.Queue()

    full = await asyncio.wait_for(c.generate_sentences([], q), timeout=2.0)

    assert full == ""
    assert log == ["attempt", "attempt"], "exactly one retry, then give up"
    assert q.get_nowait() is None, "sentinel must still arrive for the engine"


@pytest.mark.asyncio
async def test_no_retry_after_a_sentence_was_spoken(monkeypatch):
    """Retrying after audio went out would speak the answer's opening twice."""
    calls = []

    async def once(self, messages, queue, token):
        calls.append(1)
        await queue.put(SentenceEvent(text="Hello there.", is_first=True,
                                      timestamp=0.0))
        return "Hello there.", True           # emitted, then stream died

    monkeypatch.setattr(SarvamLLMClient, "_stream_once", once)
    c = SarvamLLMClient(api_key="k")
    q: asyncio.Queue = asyncio.Queue()
    full = await c.generate_sentences([], q)

    assert full == "Hello there."
    assert len(calls) == 1, "emitted output makes a retry unsafe — never retry"


# ─── 3. the caller is never ghosted ───

class _STT:
    def __init__(self): self.q = asyncio.Queue()
    async def get_event(self): return await self.q.get()
    async def flush(self): ...


class _TTS:
    def __init__(self):
        self.spoken: list[str] = []
        self._chunks: list = []

    async def reset(self): ...
    async def send_text(self, t):
        self.spoken.append(t)
        self._chunks.extend([b"\x01\x00" * 160, None])
    async def flush(self): ...
    async def get_audio(self):
        await asyncio.sleep(0.01)
        return self._chunks.pop(0) if self._chunks else None


class _DeadLLM:
    """Both attempts hang → generate_sentences yields nothing but the
    sentinel. Exactly what every call looked like during the incident."""
    async def generate_sentences(self, messages, queue):
        await queue.put(None)
        return ""

    def cancel(self): ...


@pytest.mark.asyncio
async def test_llm_outage_speaks_an_apology_not_dead_air():
    stt, tts = _STT(), _TTS()

    async def send_media(frame): ...

    engine = TurnEngine(stt=stt, llm=_DeadLLM(), tts=tts,
                        send_media=send_media, system_prompt="s",
                        greeting_text="", frame_pace_s=0)
    run = asyncio.create_task(engine.run())
    await stt.q.put(TranscriptEvent(text="what are your opening hours",
                                    is_final=True, language="en-IN",
                                    timestamp=0.0))
    await asyncio.sleep(0.6)
    await stt.q.put(None)
    await asyncio.wait_for(run, timeout=2.0)

    said = " ".join(tts.spoken)
    assert "technical issue" in said, \
        f"caller must hear an apology, heard: {tts.spoken!r}"
    # The apology is ephemeral — the model must not think it answered.
    assert all("technical issue" not in m.get("content", "")
               for m in engine.history)
    assert engine.state.value == "listening"


@pytest.mark.asyncio
async def test_apology_language_follows_the_caller():
    assert "माफ़" in _failure_line("hi-IN")
    assert "ಕ್ಷಮಿಸಿ" in _failure_line("kn-IN")
    assert "technical issue" in _failure_line("en-IN")
    assert "technical issue" in _failure_line("")        # unknown -> English
    assert "technical issue" in _failure_line("fr-FR")   # unsupported -> English


@pytest.mark.asyncio
async def test_crashed_turn_is_logged_and_state_recovers():
    """A raise inside _do_turn used to vanish into asyncio: no log line, state
    stuck, caller in dead air. The done-callback must catch it."""
    stt, tts = _STT(), _TTS()

    async def send_media(frame): ...

    engine = TurnEngine(stt=stt, llm=_DeadLLM(), tts=tts,
                        send_media=send_media, system_prompt="s",
                        greeting_text="", frame_pace_s=0)

    async def boom(transcript):
        raise RuntimeError("synthetic turn crash")

    engine._do_turn = boom
    run = asyncio.create_task(engine.run())
    await stt.q.put(TranscriptEvent(text="hello", is_final=True,
                                    language="en-IN", timestamp=0.0))
    await asyncio.sleep(0.2)
    assert engine.state.value == "listening", \
        "a crashed turn must restore LISTENING so the next utterance works"
    assert engine._pump_gate.is_set(), "audio must never stay paused"
    await stt.q.put(None)
    await asyncio.wait_for(run, timeout=2.0)
