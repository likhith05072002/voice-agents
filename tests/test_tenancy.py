"""Tests for the multi-tenant foundation: config, store, registry, factory."""

import asyncio

import pytest

from src.tenancy.agents import AgentConfig, normalize_number
from src.tenancy.store import AgentStore, load_agents_json
from src.tenancy.call_registry import CallRegistry
from src.tenancy.factory import build_engine, agent_eagerness
from src.services.stt.sarvam import TranscriptEvent
from src.services.llm.sarvam import SentenceEvent


# ─── AgentConfig ───

def test_normalize_number():
    assert normalize_number("+91 98765-43210") == "919876543210"
    assert normalize_number("") == ""


def test_config_roundtrips_through_dict():
    a = AgentConfig(agent_id="acme", name="Acme", phone_numbers=["+1 555 0100"])
    d = a.to_dict()
    assert AgentConfig.from_dict(d) == a


def test_from_dict_ignores_unknown_keys():
    a = AgentConfig.from_dict({"agent_id": "x", "name": "X", "future_field": 123})
    assert a.agent_id == "x" and a.name == "X"


def test_normalized_numbers():
    a = AgentConfig(agent_id="x", phone_numbers=["+91-98765 43210", "044 1234 5678"])
    assert a.normalized_numbers() == ["919876543210", "04412345678"]


# ─── AgentStore ───

def _store():
    return AgentStore([
        AgentConfig(agent_id="jeweller", name="Jewellery", phone_numbers=["+914012345678"]),
        AgentConfig(agent_id="clinic", name="Clinic", phone_numbers=["+12125550100"]),
    ], default_id="jeweller")


def test_resolve_by_explicit_id():
    assert _store().resolve(agent_id="clinic").agent_id == "clinic"


def test_resolve_by_phone():
    assert _store().resolve(to_number="+91 40 1234 5678").agent_id == "jeweller"


def test_resolve_by_phone_suffix_tolerates_country_code():
    # stored as +1 212 555 0100; dialed form without +1
    assert _store().resolve(to_number="2125550100").agent_id == "clinic"


def test_resolve_falls_back_to_default():
    assert _store().resolve(to_number="+9999999999").agent_id == "jeweller"


def test_resolve_raises_without_default():
    empty = AgentStore([])
    with pytest.raises(LookupError):
        empty.resolve(to_number="123")


def test_store_scales_to_many_agents():
    agents = [AgentConfig(agent_id=f"a{i}", phone_numbers=[f"+1212555{i:04d}"])
              for i in range(1000)]
    store = AgentStore(agents, default_id="a0")
    assert len(store) == 1000
    assert store.resolve(to_number="+12125550500").agent_id == "a500"   # O(1) hit
    assert store.resolve(to_number="+19999999999").agent_id == "a0"     # default


def test_load_agents_json(tmp_path):
    p = tmp_path / "agents.json"
    p.write_text('[{"agent_id": "a", "name": "A"}, {"agent_id": "b"}]', encoding="utf-8")
    agents = load_agents_json(str(p))
    assert [a.agent_id for a in agents] == ["a", "b"]


# ─── CallRegistry ───

def test_registry_put_pop():
    r = CallRegistry(ttl_s=100)
    r.put("call1", "clinic", now=0.0)
    assert r.pop("call1", now=1.0) == "clinic"
    assert r.pop("call1", now=1.0) is None      # consumed


def test_registry_expires():
    r = CallRegistry(ttl_s=10)
    r.put("c", "x", now=0.0)
    assert r.pop("c", now=50.0) is None         # past TTL


def test_registry_sweeps_stale_entries():
    r = CallRegistry(ttl_s=10)
    r.put("a", "x", now=0.0)
    r.put("b", "y", now=100.0)                  # triggers sweep of 'a'
    assert len(r) == 1


# ─── factory ───

def test_agent_eagerness_preset():
    a = AgentConfig(agent_id="x", eagerness="eager")
    assert agent_eagerness(a).min_words == 1


# ─── per-agent tools & knowledge ───

def test_catalog_builds_named_tool_set():
    from src.agent.catalog import build_tools
    reg = build_tools(["jewellery"])
    assert reg is not None
    assert any(s["function"]["name"] == "get_gold_price" for s in reg.specs())


def test_catalog_unknown_set_returns_none():
    from src.agent.catalog import build_tools
    assert build_tools(["does_not_exist"]) is None


def test_factory_uses_per_agent_knowledge_docs():
    agent = AgentConfig(agent_id="x", enable_rag=True,
                        knowledge_docs=["We sell gold bangles at festival discounts."])
    engine = build_engine(agent, stt=_STT(), llm=_CaptureLLM(), tts=_TTS(),
                          send_media=lambda f: _noop())
    assert engine.knowledge is not None
    hits = engine.knowledge.retrieve("bangles discount")
    assert any("bangles" in h.lower() for h in hits)


def test_factory_uses_per_agent_tool_sets():
    agent = AgentConfig(agent_id="x", enable_tools=True, tool_sets=["jewellery"])
    engine = build_engine(agent, stt=_STT(), llm=_CaptureLLM(), tts=_TTS(),
                          send_media=lambda f: _noop())
    assert engine.tools is not None and len(engine.tools) > 0


def test_factory_tools_disabled_when_flag_off():
    agent = AgentConfig(agent_id="x", enable_tools=False, tool_sets=["jewellery"])
    engine = build_engine(agent, stt=_STT(), llm=_CaptureLLM(), tts=_TTS(),
                          send_media=lambda f: _noop())
    assert engine.tools is None


def test_agent_llm_overrides_roundtrip():
    a = AgentConfig(agent_id="x", llm_model="sarvam-105b", llm_reasoning_effort="low",
                    transfer_numbers={"owner": "+91900"})
    back = AgentConfig.from_dict(a.to_dict())
    assert back.llm_model == "sarvam-105b"
    assert back.llm_reasoning_effort == "low"
    assert back.transfer_numbers == {"owner": "+91900"}


def test_factory_injects_per_call_context():
    agent = AgentConfig(agent_id="x", system_prompt="BASE_PROMPT")
    engine = build_engine(agent, stt=_STT(), llm=_CaptureLLM(), tts=_TTS(),
                          send_media=lambda f: _noop(),
                          extra_context="Call context — customer: Asha; reason: reminder")
    assert "BASE_PROMPT" in engine.system_prompt
    assert "Asha" in engine.system_prompt        # outbound campaign vars reach the prompt


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


async def test_factory_builds_engine_with_agent_persona():
    agent = AgentConfig(agent_id="x", system_prompt="ACME_PERSONA",
                        enable_safety=False, eagerness="eager")
    llm = _CaptureLLM()
    engine = build_engine(agent, stt=_STT(), llm=llm, tts=_TTS(),
                          send_media=lambda f: _noop())
    assert engine.min_words == 1                 # eager preset applied
    run = asyncio.create_task(engine.run())
    await engine.stt.q.put(TranscriptEvent(text="hi", is_final=True, language="en", timestamp=0.0))
    await _wait(lambda: any(h["role"] == "assistant" for h in engine.history))
    await engine.stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)
    assert "ACME_PERSONA" in llm.system          # the agent's persona drove the turn


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
