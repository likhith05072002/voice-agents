"""Unit tests for the TurnLatency instrumentation."""

import asyncio

from src.observability.metrics import TurnLatency
from src.observability import metrics as _metrics
from src.pipeline.turn_engine import TurnEngine
from src.services.stt.sarvam import TranscriptEvent, VADEvent
from src.services.llm.sarvam import SentenceEvent


def test_breakdown_computes_all_segments():
    tl = TurnLatency(turn_id=1)
    tl.user_speech_end = 0.0
    tl.transcript_in = 0.20      # 200ms STT endpoint
    tl.turn_start = 0.21         # 10ms queue
    tl.llm_first_token = 0.55    # 340ms LLM TTFT
    tl.tts_first_audio = 0.85    # 300ms TTS TTFA
    tl.first_frame_out = 0.86    # 10ms to first frame

    b = tl.breakdown()
    assert b["stt_endpoint_ms"] == 200
    assert b["queue_ms"] == 10
    assert b["llm_ttft_ms"] == 340
    assert b["tts_ttfa_ms"] == 300
    assert b["tts_to_frame_ms"] == 10
    assert b["perceived_ms"] == 860   # caller-stop -> first frame out


def test_perceived_falls_back_to_transcript_when_no_vad():
    tl = TurnLatency(turn_id=2)
    tl.transcript_in = 1.0
    tl.first_frame_out = 1.7
    b = tl.breakdown()
    assert "stt_endpoint_ms" not in b      # no VAD anchor
    assert b["perceived_ms"] == 700        # transcript -> first frame


def test_mark_is_first_write_wins():
    tl = TurnLatency(turn_id=3)
    tl.mark("first_frame_out", when=5.0)
    tl.mark("first_frame_out", when=9.0)   # ignored
    assert tl.first_frame_out == 5.0


def test_missing_stages_drop_out():
    tl = TurnLatency(turn_id=4)
    tl.transcript_in = 0.0
    # nothing else set
    assert tl.breakdown() == {}


def test_negative_segment_is_dropped():
    tl = TurnLatency(turn_id=5)
    tl.turn_start = 1.0
    tl.llm_first_token = 0.5    # out of order -> not a valid segment
    assert "llm_ttft_ms" not in tl.breakdown()


def test_emit_is_idempotent():
    tl = TurnLatency(turn_id=6)
    tl.transcript_in = 0.0
    tl.first_frame_out = 0.5
    first = tl.emit()
    assert tl.emitted is True
    second = tl.emit()             # logs nothing the second time
    assert first == second == {"perceived_ms": 500}


# ─── live-call regression: metrics must survive interrupts and hangups ───


async def test_metrics_emitted_at_first_frame_even_if_interrupted(monkeypatch):
    """The live call lost BOTH turns' metrics (greeting interrupted, turn 2
    hung up mid-playback). Metrics must now emit at first-frame-out, so a later
    interrupt cannot void them."""
    captured = []
    orig_emit = _metrics.TurnLatency.emit

    def spy(self):
        b = orig_emit(self)
        captured.append((self.kind, dict(b)))
        return b

    monkeypatch.setattr(_metrics.TurnLatency, "emit", spy)

    stt = _FakeSTT()
    engine = TurnEngine(
        stt=stt, llm=_LongLLM(), tts=_BigTTS(), send_media=lambda f: _noop(),
        system_prompt="sys", greeting_text="", frame_pace_s=0.005,
    )
    run = asyncio.create_task(engine.run())
    await stt.q.put(TranscriptEvent(text="hi", is_final=True, language="en", timestamp=0.0))
    # wait until audio is flowing (metrics were published at first frame)
    while not captured:
        await asyncio.sleep(0.005)
    # now interrupt mid-playback — previously this VOIDED the metrics
    await stt.q.put(TranscriptEvent(text="what about silver price today",
                                    is_final=True, language="en", timestamp=0.0))
    await stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)

    turn_metrics = [b for kind, b in captured if kind == "turn"]
    assert turn_metrics, "metrics lost despite first frame having played"
    assert "perceived_ms" in turn_metrics[0]


class _LongLLM:
    async def generate_sentences(self, messages, queue):
        await queue.put(SentenceEvent(text="A long answer that keeps going. ",
                                      is_first=True, timestamp=0.0))
        await queue.put(None)
        return "x"
    def cancel(self): ...


class _BigTTS:
    def __init__(self): self._p = []
    async def reset(self): self._p = []
    async def send_text(self, t): self._p = [b"\x01\x00" * 32000, None]  # long playback
    async def flush(self): ...
    async def get_audio(self): return self._p.pop(0) if self._p else None


async def _noop():
    return None


# ─── engine integration: prove the wiring fires on a real turn ───


class _FakeSTT:
    def __init__(self):
        self.q: asyncio.Queue = asyncio.Queue()

    async def get_event(self):
        return await self.q.get()


class _FakeLLM:
    def __init__(self, sentences):
        self.sentences = sentences
        self.cancelled = False

    async def generate_sentences(self, messages, queue):
        for s in self.sentences:
            await queue.put(SentenceEvent(text=s, is_first=False, timestamp=0.0))
        await queue.put(None)
        return "".join(self.sentences)

    def cancel(self):
        self.cancelled = True


class _FakeTTS:
    def __init__(self):
        self._pending = []

    async def reset(self):
        self._pending = []

    async def send_text(self, text):
        self._pending = [b"\x01\x00" * 160, None]

    async def flush(self):
        pass

    async def get_audio(self):
        return self._pending.pop(0) if self._pending else None


async def test_engine_emits_turn_latency(monkeypatch):
    """A full turn must emit a populated 'turn' breakdown with the LLM and
    perceived stages stamped — proves every hook in the engine fires."""
    captured: list[tuple[str, dict]] = []
    orig_emit = _metrics.TurnLatency.emit

    def spy(self):
        b = orig_emit(self)
        captured.append((self.kind, b))
        return b

    monkeypatch.setattr(_metrics.TurnLatency, "emit", spy)

    stt, llm, tts, sent = _FakeSTT(), _FakeLLM(["Hello there. "]), _FakeTTS(), []

    async def send_media(frame):
        sent.append(frame)

    engine = TurnEngine(
        stt=stt, llm=llm, tts=tts, send_media=send_media,
        system_prompt="sys", greeting_text="", frame_pace_s=0,
    )
    run = asyncio.create_task(engine.run())
    # caller stops (END_SPEECH), then the transcript lands
    await stt.q.put(VADEvent(is_speech_start=False, timestamp=0.0))
    await stt.q.put(TranscriptEvent(text="hi", is_final=True, language="en", timestamp=0.0))
    await stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)

    turns = [b for kind, b in captured if kind == "turn"]
    assert turns, "no turn.latency emitted"
    b = turns[0]
    assert "llm_ttft_ms" in b          # turn_start -> first LLM sentence
    assert "tts_ttfa_ms" in b          # first LLM sentence -> first TTS audio
    assert "perceived_ms" in b         # caller-stop -> first frame out
    assert b["perceived_ms"] >= 0
