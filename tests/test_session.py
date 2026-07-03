"""Tests for agents, routing/handoff, session config, and transcript sink."""

import asyncio

from src.agent.session import Agent, AgentRouter, SessionConfig, build_demo_router
from src.pipeline.turn_engine import TurnEngine
from src.services.stt.sarvam import TranscriptEvent
from src.services.llm.sarvam import SentenceEvent


# ─── router ───

def test_router_defaults_to_default_agent():
    r = build_demo_router("sales prompt", "support prompt")
    assert r.agent.name == "sales"


def test_router_switches_on_keyword():
    r = build_demo_router("sales prompt", "support prompt")
    assert r.route("my ring is broken and I want a repair").name == "support"


def test_router_is_sticky_until_new_match():
    r = build_demo_router("sales prompt", "support prompt")
    r.route("I have a complaint")           # -> support
    assert r.route("ok thanks").name == "support"   # neutral stays on support
    assert r.route("I want to buy a new chain").name == "sales"   # switches back


def test_router_explicit_handoff():
    r = build_demo_router("s", "t")
    assert r.handoff("support") is True
    assert r.agent.name == "support"
    assert r.handoff("nonexistent") is False


def test_router_rejects_bad_default():
    import pytest
    with pytest.raises(ValueError):
        AgentRouter([Agent("a", "p")], default="missing")


def test_session_config_defaults_and_override():
    c = SessionConfig()
    assert c.language == "te-IN" and c.voice == "anushka"
    c2 = SessionConfig(language="hi-IN", voice="vidya", enable_tools=True)
    assert c2.language == "hi-IN" and c2.enable_tools is True


# ─── engine integration ───

class _STT:
    def __init__(self): self.q = asyncio.Queue()
    async def get_event(self): return await self.q.get()


class _CaptureLLM:
    def __init__(self): self.system = ""
    async def generate_sentences(self, messages, queue):
        self.system = messages[0]["content"]
        await queue.put(SentenceEvent(text="ok. ", is_first=True, timestamp=0.0))
        await queue.put(None)
        return "ok. "
    def cancel(self): ...


class _TTS:
    def __init__(self): self._p = []
    async def reset(self): self._p = []
    async def send_text(self, t): self._p = [b"\x01\x00" * 160, None]
    async def flush(self): ...
    async def get_audio(self): return self._p.pop(0) if self._p else None


async def test_engine_uses_routed_agent_prompt_and_emits_transcript():
    router = build_demo_router("SALES_PERSONA", "SUPPORT_PERSONA")
    llm = _CaptureLLM()
    captured: list[tuple[str, str]] = []
    engine = TurnEngine(stt=_STT(), llm=llm, tts=_TTS(),
                        send_media=lambda f: _noop(),
                        system_prompt="base", greeting_text="", frame_pace_s=0,
                        router=router, on_transcript=lambda role, txt: captured.append((role, txt)))
    run = asyncio.create_task(engine.run())
    await engine.stt.q.put(TranscriptEvent(text="my chain is broken, need a repair",
                                           is_final=True, language="en", timestamp=0.0))
    await _wait(lambda: any(h["role"] == "assistant" for h in engine.history))
    await engine.stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)

    assert "SUPPORT_PERSONA" in llm.system           # routed to support agent
    assert ("user", "my chain is broken, need a repair") in captured
    assert ("assistant", "ok. ") in captured


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
