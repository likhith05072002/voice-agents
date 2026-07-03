"""Tests for language-aware TTS switching."""

import asyncio

from src.services.tts.sarvam import SarvamTTSClient
from src.pipeline.turn_engine import TurnEngine
from src.services.stt.sarvam import TranscriptEvent
from src.services.llm.sarvam import SentenceEvent


def test_needs_language_switch_logic():
    c = SarvamTTSClient("k")
    c._language = "te-IN"
    assert c._needs_language_switch("hi-IN") is True
    assert c._needs_language_switch("te-IN") is False
    assert c._needs_language_switch("") is False


class _STT:
    def __init__(self): self.q = asyncio.Queue()
    async def get_event(self): return await self.q.get()


class _LLM:
    def __init__(self): self.messages = []
    async def generate_sentences(self, messages, queue):
        self.messages.append(messages)
        await queue.put(SentenceEvent(text="ok. ", is_first=True, timestamp=0.0))
        await queue.put(None)
        return "ok. "
    def cancel(self): ...


class _LangTTS:
    def __init__(self): self.langs = []; self._p = []
    async def reset(self): self._p = []
    async def send_text(self, t): self._p = [b"\x01\x00" * 160, None]
    async def flush(self): ...
    async def get_audio(self): return self._p.pop(0) if self._p else None
    async def ensure_language(self, lang): self.langs.append(lang)


async def test_engine_switches_tts_language_to_caller():
    tts = _LangTTS()
    sent = []
    engine = TurnEngine(stt=_STT(), llm=_LLM(), tts=tts,
                        send_media=lambda f: sent.append(f) or _noop(),
                        system_prompt="s", greeting_text="", frame_pace_s=0,
                        enable_language_switch=True)
    run = asyncio.create_task(engine.run())
    await engine.stt.q.put(TranscriptEvent(text="namaste", is_final=True,
                                           language="hi-IN", timestamp=0.0))
    await _wait(lambda: any(h["role"] == "assistant" for h in engine.history))
    await engine.stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)
    assert "hi-IN" in tts.langs


async def test_no_switch_when_disabled():
    tts = _LangTTS()
    sent = []
    engine = TurnEngine(stt=_STT(), llm=_LLM(), tts=tts,
                        send_media=lambda f: sent.append(f) or _noop(),
                        system_prompt="s", greeting_text="", frame_pace_s=0,
                        enable_language_switch=False)
    run = asyncio.create_task(engine.run())
    await engine.stt.q.put(TranscriptEvent(text="namaste", is_final=True,
                                           language="hi-IN", timestamp=0.0))
    await _wait(lambda: any(h["role"] == "assistant" for h in engine.history))
    await engine.stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)
    assert tts.langs == []


async def test_llm_told_to_reply_in_caller_language():
    tts = _LangTTS()
    llm = _LLM()
    sent = []
    engine = TurnEngine(stt=_STT(), llm=llm, tts=tts,
                        send_media=lambda f: sent.append(f) or _noop(),
                        system_prompt="s", greeting_text="", frame_pace_s=0,
                        enable_language_switch=True)
    run = asyncio.create_task(engine.run())
    await engine.stt.q.put(TranscriptEvent(text="ನಮಸ್ಕಾರ", is_final=True,
                                           language="kn-IN", timestamp=0.0))
    await _wait(lambda: any(h["role"] == "assistant" for h in engine.history))
    await engine.stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)
    last = llm.messages[0][-1]
    assert last["role"] == "system" and "Kannada" in last["content"]


async def test_llm_not_told_when_switch_disabled():
    tts = _LangTTS()
    llm = _LLM()
    sent = []
    engine = TurnEngine(stt=_STT(), llm=llm, tts=tts,
                        send_media=lambda f: sent.append(f) or _noop(),
                        system_prompt="s", greeting_text="", frame_pace_s=0,
                        enable_language_switch=False)
    run = asyncio.create_task(engine.run())
    await engine.stt.q.put(TranscriptEvent(text="ನಮಸ್ಕಾರ", is_final=True,
                                           language="kn-IN", timestamp=0.0))
    await _wait(lambda: any(h["role"] == "assistant" for h in engine.history))
    await engine.stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)
    assert all("Kannada" not in m["content"] for m in llm.messages[0])


class _FlipLLM:
    """Returns English on the first call, Kannada on the retry."""
    def __init__(self):
        self.calls = 0
        self.cancelled = 0
    async def generate_sentences(self, messages, queue):
        self.calls += 1
        text = ("We are a technology company. " if self.calls == 1
                else "ನಾವು ತಂತ್ರಜ್ಞಾನ ಕಂಪನಿ. ")
        await queue.put(SentenceEvent(text=text, is_first=True, timestamp=0.0))
        await queue.put(None)
        return text
    def cancel(self): self.cancelled += 1


async def test_language_guard_regenerates_wrong_language_reply():
    tts = _LangTTS()
    llm = _FlipLLM()
    sent = []
    spoken = []
    engine = TurnEngine(stt=_STT(), llm=llm, tts=tts,
                        send_media=lambda f: sent.append(f) or _noop(),
                        system_prompt="s", greeting_text="", frame_pace_s=0,
                        enable_language_switch=True,
                        on_transcript=lambda role, t: spoken.append((role, t)))
    run = asyncio.create_task(engine.run())
    await engine.stt.q.put(TranscriptEvent(text="ನಿಮ್ಮ ಸೇವೆಗಳ ಬಗ್ಗೆ ಹೇಳಿ", is_final=True,
                                           language="kn-IN", timestamp=0.0))
    await _wait(lambda: any(h["role"] == "assistant" for h in engine.history))
    await engine.stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)
    assert llm.calls == 2                        # retried once
    said = " ".join(t for r, t in spoken if r == "assistant")
    assert "technology company" not in said      # English draft never spoken
    assert "ತಂತ್ರಜ್ಞಾನ" in said                   # Kannada retry was


def test_script_matches_detects_language():
    from src.testing.runner import _script_matches
    kn = "ನಮ್ಮ ಕಂಪನಿ ಆಸ್ಟಿನ್ ನಗರದಲ್ಲಿದೆ. Cocolevio 2015."
    assert _script_matches(kn, "kn-IN") is True
    assert _script_matches(kn, "en-IN") is False
    en = "We are based in Austin, Texas."
    assert _script_matches(en, "en-IN") is True
    assert _script_matches(en, "kn-IN") is False
    assert _script_matches("", "kn-IN") is False


async def _noop():
    return None


async def _wait(pred, timeout=2.0):
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if pred():
            return True
        await asyncio.sleep(0.005)
    raise AssertionError("condition not met")
