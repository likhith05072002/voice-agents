"""Repeatable, network-free benchmarks for the turn engine.

These measure the things our engineering loop actually changed — playback pacing
accuracy and first-audio chunking — plus the engine's own per-turn compute
overhead. They do NOT measure real STT/LLM/TTS latency (that is network-bound and
needs a live call + the `turn.latency` logs). Run:

    python scripts/bench_pipeline.py
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Windows consoles default to cp1252 and choke on box-drawing / Indic glyphs.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import logging
import structlog

# Quiet the per-turn INFO logs so the benchmark output is readable.
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))

from src.services.llm.sarvam import _flush_boundary
from src.pipeline.turn_engine import TurnEngine
from src.services.stt.sarvam import TranscriptEvent
from src.services.llm.sarvam import SentenceEvent


# ─── 1. playback pacing: deadline-chasing vs naive per-frame sleep ───

async def _pace(n: int, pace_s: float, mode: str) -> float:
    loop = asyncio.get_event_loop()
    next_send = None
    t0 = time.perf_counter()
    for _ in range(n):
        if mode == "deadline":
            now = loop.time()
            if next_send is None or now - next_send > 0.2:
                next_send = now
            delay = next_send - now
            if delay > 0:
                await asyncio.sleep(delay)
            next_send += pace_s
        else:
            await asyncio.sleep(pace_s)
    return time.perf_counter() - t0


async def bench_pacing() -> None:
    n, pace_s = 50, 0.02            # 1.000s of 20ms telephony frames
    ideal = n * pace_s
    old = await _pace(n, pace_s, "naive")
    new = await _pace(n, pace_s, "deadline")
    print("── Playback pacing (1.0s of audio) ──")
    print(f"  ideal real-time : {ideal:.3f}s")
    print(f"  naive sleep     : {old:.3f}s  ({old / ideal * 100:.0f}% of real-time)")
    print(f"  deadline (ours) : {new:.3f}s  ({new / ideal * 100:.0f}% of real-time)")


# ─── 2. first-audio: clause-early first chunk vs sentence-only ───

def _first_flush_index(text: str, clause_early: bool) -> int:
    buf = ""
    for i, ch in enumerate(text, start=1):
        buf += ch
        if _flush_boundary(buf, is_first=clause_early):
            return i
    return len(text)


def bench_first_chunk() -> None:
    samples = [
        "Namaskaram, Nama Srinivasa Jewellery ki swagatam. Meeku emi sahayam?",
        "Sure, the price for twenty two carat gold today is 7150 rupees.",
    ]
    print("── First-audio chunk start (char index, lower = sooner) ──")
    for s in samples:
        new = _first_flush_index(s, clause_early=True)
        old = _first_flush_index(s, clause_early=False)
        print(f"  clause-early {new:>3}  vs  sentence-only {old:>3}  ({old - new:+d} chars sooner) | {s[:40]}…")


# ─── 3. engine per-turn compute overhead (no pacing, no network) ───

class _STT:
    def __init__(self): self.q = asyncio.Queue()
    async def get_event(self): return await self.q.get()


class _LLM:
    async def generate_sentences(self, messages, queue):
        await queue.put(SentenceEvent(text="Short reply. ", is_first=True, timestamp=0.0))
        await queue.put(None)
        return "Short reply. "
    def cancel(self): ...


class _TTS:
    def __init__(self): self._p = []
    async def reset(self): self._p = []
    async def send_text(self, t): self._p = [b"\x01\x00" * 160, None]
    async def flush(self): ...
    async def get_audio(self): return self._p.pop(0) if self._p else None


async def bench_engine_overhead(turns: int = 200) -> None:
    sent = []
    engine = TurnEngine(stt=_STT(), llm=_LLM(), tts=_TTS(),
                        send_media=lambda f: sent.append(f) or _ayield(),
                        system_prompt="s", greeting_text="", frame_pace_s=0)
    run = asyncio.create_task(engine.run())
    t0 = time.perf_counter()
    for i in range(turns):
        engine.history.clear()
        await engine.stt.q.put(TranscriptEvent(text="hi", is_final=True, language="en", timestamp=0.0))
        # wait for this turn to be recorded
        while not any(h["role"] == "assistant" for h in engine.history):
            await asyncio.sleep(0)
    dt = time.perf_counter() - t0
    await engine.stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)
    print("── Engine per-turn compute overhead (fakes, no pacing/network) ──")
    print(f"  {turns} turns in {dt * 1000:.1f}ms  ->  {dt / turns * 1000:.3f}ms/turn")


async def _ayield():
    return None


# ─── 4. barge-in: time from VAD onset to the agent going silent ───

async def bench_barge_in() -> None:
    from src.pipeline.turn_engine import TurnEngine
    from src.services.stt.sarvam import TranscriptEvent, VADEvent
    from src.services.llm.sarvam import SentenceEvent

    sends: list[float] = []

    async def send_media(frame):
        sends.append(time.perf_counter())

    class _STT:
        def __init__(self): self.q = asyncio.Queue()
        async def get_event(self): return await self.q.get()

    class _LLM:
        async def generate_sentences(self, m, q):
            await q.put(SentenceEvent("A fairly long answer that continues for a while. ", True, 0.0))
            await q.put(None)
            return "x"
        def cancel(self): ...

    class _TTS:
        def __init__(self): self._p = []
        async def reset(self): self._p = []
        async def send_text(self, t): self._p = [b"\x01\x00" * 16000, None]  # ~100 frames
        async def flush(self): ...
        async def get_audio(self): return self._p.pop(0) if self._p else None

    eng = TurnEngine(stt=_STT(), llm=_LLM(), tts=_TTS(), send_media=send_media,
                     system_prompt="s", greeting_text="", frame_pace_s=0.02)
    run = asyncio.create_task(eng.run())
    await eng.stt.q.put(TranscriptEvent(text="hi", is_final=True, language="en", timestamp=0.0))
    while len(sends) < 5:
        await asyncio.sleep(0.002)
    t_vad = time.perf_counter()
    await eng.stt.q.put(VADEvent(is_speech_start=True, timestamp=0.0))   # caller cuts in
    # wait until frames stop arriving (silence)
    while time.perf_counter() - sends[-1] < 0.05:
        await asyncio.sleep(0.002)
    time_to_silence = (sends[-1] - t_vad) * 1000
    run.cancel()
    try:
        await run
    except asyncio.CancelledError:
        pass
    print("── Barge-in responsiveness ──")
    print(f"  VAD onset -> agent silent: {time_to_silence:.1f}ms "
          f"(target <100ms; one frame = 20ms)")


# ─── 5. concurrency stress: many simultaneous turns ───

async def bench_concurrency(sessions: int = 50) -> None:
    from src.pipeline.turn_engine import TurnEngine
    from src.services.stt.sarvam import TranscriptEvent
    from src.services.llm.sarvam import SentenceEvent

    class _STT:
        def __init__(self): self.q = asyncio.Queue()
        async def get_event(self): return await self.q.get()

    class _LLM:
        async def generate_sentences(self, m, q):
            await q.put(SentenceEvent("Reply. ", True, 0.0))
            await q.put(None)
            return "Reply. "
        def cancel(self): ...

    class _TTS:
        def __init__(self): self._p = []
        async def reset(self): self._p = []
        async def send_text(self, t): self._p = [b"\x01\x00" * 160, None]
        async def flush(self): ...
        async def get_audio(self): return self._p.pop(0) if self._p else None

    async def one_session():
        eng = TurnEngine(stt=_STT(), llm=_LLM(), tts=_TTS(),
                         send_media=lambda f: _ayield(), system_prompt="s",
                         greeting_text="", frame_pace_s=0)
        run = asyncio.create_task(eng.run())
        await eng.stt.q.put(TranscriptEvent(text="hi", is_final=True, language="en", timestamp=0.0))
        while not any(h["role"] == "assistant" for h in eng.history):
            await asyncio.sleep(0)
        await eng.stt.q.put(None)
        await asyncio.wait_for(run, timeout=5.0)

    t0 = time.perf_counter()
    await asyncio.gather(*(one_session() for _ in range(sessions)))
    dt = (time.perf_counter() - t0) * 1000
    print("── Concurrency stress (simultaneous calls, no network) ──")
    print(f"  {sessions} concurrent sessions completed in {dt:.0f}ms, 0 errors")


# ─── 6. tool-turn: redundant LLM round-trip eliminated ───

async def bench_tool_turn() -> None:
    from src.pipeline.turn_engine import TurnEngine
    from src.services.stt.sarvam import TranscriptEvent
    from src.services.llm.sarvam import SentenceEvent
    from src.agent.tools import Tool, ToolRegistry

    class _STT:
        def __init__(self): self.q = asyncio.Queue()
        async def get_event(self): return await self.q.get()

    class _LLM:
        def __init__(self): self.complete_calls = 0; self.stream_calls = 0
        async def complete(self, messages, tools=None):
            self.complete_calls += 1
            await asyncio.sleep(0.1)                 # models one LLM round-trip
            return "The 22k gold price is 7150 rupees.", []   # no tool needed
        async def generate_sentences(self, m, q):
            self.stream_calls += 1
            await asyncio.sleep(0.1)
            await q.put(SentenceEvent("The 22k gold price is 7150 rupees. ", True, 0.0))
            await q.put(None)
            return "x"
        def cancel(self): ...

    class _TTS:
        def __init__(self): self._p = []
        async def reset(self): self._p = []
        async def send_text(self, t): self._p = [b"\x01\x00" * 160, None]
        async def flush(self): ...
        async def get_audio(self): return self._p.pop(0) if self._p else None

    reg = ToolRegistry()
    reg.register(Tool("get_gold_price", "price", {"type": "object", "properties": {}},
                      lambda a: "7150"))
    llm = _LLM()
    eng = TurnEngine(stt=_STT(), llm=llm, tts=_TTS(), send_media=lambda f: _ayield(),
                     system_prompt="s", greeting_text="", frame_pace_s=0, tools=reg)
    run = asyncio.create_task(eng.run())
    t0 = time.perf_counter()
    await eng.stt.q.put(TranscriptEvent(text="22k price?", is_final=True, language="en", timestamp=0.0))
    while not any(h["role"] == "assistant" for h in eng.history):
        await asyncio.sleep(0.002)
    dt = (time.perf_counter() - t0) * 1000
    await eng.stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)
    print("── Tool-enabled turn (LLM round-trips) ──")
    print(f"  {llm.complete_calls} LLM call(s) + {llm.stream_calls} stream, {dt:.0f}ms "
          f"(old path: 2 LLM calls ~200ms — a full round-trip saved)")


async def main() -> None:
    await bench_pacing()
    print()
    bench_first_chunk()
    print()
    await bench_engine_overhead()
    print()
    await bench_barge_in()
    print()
    await bench_concurrency()
    print()
    await bench_tool_turn()


if __name__ == "__main__":
    asyncio.run(main())
