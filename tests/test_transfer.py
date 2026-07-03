"""Tests for human-handoff call transfer."""

import asyncio
import json

import httpx
import pytest

from src.agent.transfer import build_transfer_tool, attach_transfer_tool
from src.agent.tools import ToolRegistry
from src.telephony.telnyx import TelnyxClient
from src.tenancy.agents import AgentConfig
from src.pipeline.turn_engine import TurnEngine
from src.services.stt.sarvam import TranscriptEvent
from src.services.llm.sarvam import SentenceEvent


# ─── TelnyxClient.transfer ───

async def test_telnyx_transfer_posts_to_action():
    seen = []

    def handler(request):
        seen.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={})

    tc = TelnyxClient("k", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await tc.transfer("cc1", to="+15550001111")
    url, body = seen[0]
    assert url.endswith("/calls/cc1/actions/transfer")
    assert body == {"to": "+15550001111"}


async def test_telnyx_transfer_raises_on_error():
    def handler(request):
        return httpx.Response(422, json={})

    tc = TelnyxClient("k", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(RuntimeError):
        await tc.transfer("cc1", to="+1")


# ─── the tool itself ───

def _tool(deferred, transferred):
    async def transfer_action(number):
        transferred.append(number)
    return build_transfer_tool(
        transfer_numbers={"owner": "+919000000001", "billing": "+919000000002"},
        defer=lambda a: deferred.append(a),
        transfer_action=transfer_action,
    )


async def test_tool_schedules_known_destination():
    deferred, transferred = [], []
    tool = _tool(deferred, transferred)
    out = tool.handler({"destination": "Owner"})           # case-insensitive
    assert out["status"] == "transfer_scheduled"
    assert len(deferred) == 1 and not transferred          # scheduled, not executed
    await deferred[0]()                                    # post-playback execution
    assert transferred == ["+919000000001"]


def test_tool_rejects_unknown_destination():
    deferred, transferred = [], []
    out = _tool(deferred, transferred).handler({"destination": "ceo"})
    assert "error" in out
    assert out["available_destinations"] == ["billing", "owner"]
    assert not deferred


def test_tool_spec_enumerates_destinations():
    tool = _tool([], [])
    assert tool.spec()["function"]["parameters"]["properties"]["destination"]["enum"] == \
        ["billing", "owner"]


def test_attach_noop_without_numbers():
    class _E:
        tools = None
        def defer_after_turn(self, a): ...
    e = _E()
    attach_transfer_tool(e, AgentConfig(agent_id="x"), transfer_action=None)
    assert e.tools is None


# ─── the ordering property: transfer fires AFTER the audio played ───

class _STT:
    def __init__(self): self.q = asyncio.Queue()
    async def get_event(self): return await self.q.get()


class _TransferLLM:
    """Round 1: calls transfer_call. Round 2: speaks the confirmation."""
    def __init__(self): self.n = 0
    async def complete(self, messages, tools=None):
        self.n += 1
        if self.n == 1:
            return None, [{"id": "1", "function": {
                "name": "transfer_call",
                "arguments": json.dumps({"destination": "owner"})}}]
        return "Connecting you to the owner now.", []
    async def generate_sentences(self, m, q):
        await q.put(None)
        return ""
    def cancel(self): ...


class _TTS:
    def __init__(self): self._p = []
    async def reset(self): self._p = []
    async def send_text(self, t): self._p = [b"\x01\x00" * 160, None]
    async def flush(self): ...
    async def get_audio(self): return self._p.pop(0) if self._p else None


async def test_transfer_executes_after_confirmation_played():
    events = []                      # interleaving proof

    async def send_media(frame):
        events.append("audio")

    async def transfer_action(number):
        events.append(f"transfer:{number}")

    engine = TurnEngine(stt=_STT(), llm=_TransferLLM(), tts=_TTS(),
                        send_media=send_media, system_prompt="s", greeting_text="",
                        frame_pace_s=0, tools=ToolRegistry())
    attach_transfer_tool(engine,
                         AgentConfig(agent_id="x", transfer_numbers={"owner": "+91900"}),
                         transfer_action=transfer_action)
    run = asyncio.create_task(engine.run())
    await engine.stt.q.put(TranscriptEvent(text="can I talk to the owner",
                                           is_final=True, language="en", timestamp=0.0))
    while "transfer:+91900" not in events:
        await asyncio.sleep(0.005)
    await engine.stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)

    # every audio frame precedes the transfer — the caller heard the confirmation
    assert events.index("transfer:+91900") == len(events) - 1
    assert "audio" in events
    # the confirmation was recorded as the assistant's final words
    assert engine.history[-1]["content"] == "Connecting you to the owner now."
