"""Tests for tool registry + tool-resolution runner."""

import json

import pytest

from src.agent.tools import Tool, ToolRegistry
from src.agent.runner import resolve_tools


def _price_args(karat):
    return {"type": "object", "properties": {"karat": {"type": "integer"}}, "required": ["karat"]}


def _make_registry():
    reg = ToolRegistry()

    @reg.tool("get_gold_price", "Get today's gold price per gram", _price_args(0))
    def get_gold_price(args):
        return {"karat": args["karat"], "price_per_gram": 7800 if args["karat"] == 24 else 7150}

    return reg


# ─── registry ───

def test_registry_specs_are_openai_shaped():
    reg = _make_registry()
    spec = reg.specs()[0]
    assert spec["type"] == "function"
    assert spec["function"]["name"] == "get_gold_price"
    assert "parameters" in spec["function"]


async def test_registry_call_json_encodes_dict_result():
    reg = _make_registry()
    out = await reg.call("get_gold_price", {"karat": 24})
    assert json.loads(out)["price_per_gram"] == 7800


async def test_registry_unknown_tool_returns_error_not_raise():
    reg = _make_registry()
    out = await reg.call("nope", {})
    assert "error" in json.loads(out)


async def test_registry_handler_exception_is_captured():
    reg = ToolRegistry()
    reg.register(Tool("boom", "raises", {"type": "object", "properties": {}},
                      lambda args: (_ for _ in ()).throw(ValueError("bad"))))
    out = await reg.call("boom", {})
    assert json.loads(out)["error"] == "bad"


async def test_registry_supports_async_handler():
    reg = ToolRegistry()

    async def h(args):
        return "async-ok"

    reg.register(Tool("a", "d", {"type": "object", "properties": {}}, h))
    assert await reg.call("a", {}) == "async-ok"


# ─── runner ───

async def test_resolve_tools_passthrough_when_no_tools_registered():
    msgs = [{"role": "user", "content": "hi"}]

    async def complete(messages, tools):
        raise AssertionError("should not call LLM when registry empty")

    out_msgs, answer = await resolve_tools(complete, msgs, ToolRegistry())
    assert out_msgs == msgs
    assert answer is None


async def test_resolve_tools_executes_then_finishes():
    reg = _make_registry()
    calls = {"n": 0}

    async def complete(messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return None, [{
                "id": "c1",
                "function": {"name": "get_gold_price", "arguments": json.dumps({"karat": 22})},
            }]
        return "It is 7150 rupees.", []   # second round: no more tools

    out, answer = await resolve_tools(complete, [{"role": "user", "content": "22k price?"}], reg)
    # augmented with the assistant tool call + the tool result
    roles = [m["role"] for m in out]
    assert "assistant" in roles and "tool" in roles
    tool_msg = next(m for m in out if m["role"] == "tool")
    assert json.loads(tool_msg["content"])["price_per_gram"] == 7150
    assert calls["n"] == 2
    assert answer == "It is 7150 rupees."          # spoken directly, no 2nd call


async def test_resolve_tools_respects_max_rounds():
    reg = _make_registry()

    async def always_calls(messages, tools):
        return None, [{
            "id": "x",
            "function": {"name": "get_gold_price", "arguments": json.dumps({"karat": 24})},
        }]

    out, answer = await resolve_tools(always_calls, [{"role": "user", "content": "?"}], reg, max_rounds=2)
    # 2 rounds -> 2 assistant + 2 tool messages appended (no infinite loop)
    assert sum(1 for m in out if m["role"] == "tool") == 2
    assert answer is None                          # exhausted -> caller streams


# ─── engine integration: a registered tool actually runs during a turn ───

import asyncio  # noqa: E402

from src.pipeline.turn_engine import TurnEngine  # noqa: E402
from src.services.stt.sarvam import TranscriptEvent  # noqa: E402
from src.services.llm.sarvam import SentenceEvent  # noqa: E402


class _STT:
    def __init__(self): self.q = asyncio.Queue()
    async def get_event(self): return await self.q.get()


class _TTS:
    def __init__(self): self._p = []
    async def reset(self): self._p = []
    async def send_text(self, t): self._p = [b"\x01\x00" * 160, None]
    async def flush(self): ...
    async def get_audio(self): return self._p.pop(0) if self._p else None


class _ToolLLM:
    def __init__(self): self.complete_calls = 0
    async def complete(self, messages, tools=None):
        self.complete_calls += 1
        if self.complete_calls == 1:
            return None, [{"id": "1", "function": {"name": "ping", "arguments": "{}"}}]
        return "ok", []
    async def generate_sentences(self, messages, queue):
        await queue.put(SentenceEvent(text="Answer. ", is_first=True, timestamp=0.0))
        await queue.put(None)
        return "Answer. "
    def cancel(self): ...


async def test_engine_runs_tool_during_turn():
    ran = {"ping": False}
    reg = ToolRegistry()

    def ping(args):
        ran["ping"] = True
        return "pong"
    reg.register(Tool("ping", "ping", {"type": "object", "properties": {}}, ping))

    llm = _ToolLLM()
    sent = []
    engine = TurnEngine(stt=_STT(), llm=llm, tts=_TTS(),
                        send_media=lambda f: sent.append(f) or _noop(),
                        system_prompt="s", greeting_text="", frame_pace_s=0, tools=reg)
    run = asyncio.create_task(engine.run())
    await engine.stt.q.put(TranscriptEvent(text="ping please", is_final=True, language="en", timestamp=0.0))
    await _wait(lambda: any(h["role"] == "assistant" for h in engine.history))
    await engine.stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)

    assert ran["ping"] is True               # the tool executed
    assert llm.complete_calls >= 2           # decision round + finish round
    # The answer is spoken from the tool-decision completion ("ok"), NOT from a
    # second streaming generate_sentences call — that round-trip is eliminated.
    assert engine.history[-1] == {"role": "assistant", "content": "ok"}


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


async def test_prefetch_metal_intent_any_language():
    """Deterministic prefetch: metal words in any language inject live rates —
    the model never gets to guess a price."""
    from src.agent.demo_tools import _METAL_RE, _AFFAIRS_RE, _QTY_RE
    for t in ["what is the gold price", "ಒಂದು ಗ್ರಾಂ ಗೋಲ್ಡ್ ಬೆಲೆ ಎಷ್ಟು?",
              "ನನಗೆ 40 ಗ್ರಾಂ ಗೋಲ್ಡ್ ಬಿಸ್ಕೆಟ್ ಬೇಕಿತ್ತು", "चांदी की कीमत",
              "బంగారం ధర ఎంత?", "தங்கம் விலை"]:
        assert _METAL_RE.search(t), t
    assert not _METAL_RE.search("book me a cleaning on Saturday")
    m = _QTY_RE.search("ನನಗೆ 40 ಗ್ರಾಂ ಬೇಕು")
    assert m and m.group(1) == "40"
    for t in ["who is the President of United States",
              "ಭಾರತದ ಪ್ರಧಾನ ಮಂತ್ರಿ ಯಾರು", "मुख्यमंत्री कौन है"]:
        assert _AFFAIRS_RE.search(t), t
    assert not _AFFAIRS_RE.search("what is the gold price")
