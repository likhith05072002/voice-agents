"""Latency micro-benchmarks against live Sarvam APIs.

Usage:
    python scripts/bench_latency.py stt-endpoint   # flush-forcing vs natural endpoint
    python scripts/bench_latency.py llm-ttft       # sarvam-30b vs 105b, short vs long prompt
    python scripts/bench_latency.py tts-ttfa       # time-to-first-audio per config

Each test is small and pointed: a handful of API calls, precise timings, done.
"""

from __future__ import annotations

import asyncio
import audioop
import glob
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.stt.sarvam import SarvamSTTClient, TranscriptEvent  # noqa: E402
from src.services.llm.sarvam import SarvamLLMClient, SentenceEvent  # noqa: E402
from src.services.tts.sarvam import SarvamTTSClient  # noqa: E402


def _api_key() -> str:
    if os.environ.get("SARVAM_API_KEY"):
        return os.environ["SARVAM_API_KEY"]
    with open(".env", encoding="utf-8") as f:
        for line in f:
            if line.startswith("SARVAM_API_KEY"):
                return line.split("=", 1)[1].strip()
    raise SystemExit("no SARVAM_API_KEY")


def _speech_16k() -> bytes:
    """A cached tester utterance (PCM16 8k) resampled to 16k."""
    pcms = sorted(glob.glob("data/tts_cache/*.pcm"), key=os.path.getsize)
    if not pcms:
        raise SystemExit("no cached PCM; run any test once first")
    with open(pcms[len(pcms) // 2], "rb") as f:
        pcm8 = f.read()
    pcm16, _ = audioop.ratecv(pcm8, 2, 1, 8000, 16000, None)
    return pcm16


async def _stream_utterance(stt, speech: bytes, *, flush_at_end: bool) -> float:
    """Stream one utterance at real time + trailing silence; return seconds
    from end-of-speech to the final transcript (or inf on timeout)."""
    CHUNK = int(16000 * 2 * 0.1)                     # 100ms of 16k PCM16
    for i in range(0, len(speech), CHUNK):
        await stt.send_audio(speech[i:i + CHUNK])
        await asyncio.sleep(0.1)
    t_end = time.perf_counter()
    if flush_at_end:
        await stt.flush()

    async def _silence_feeder():
        quiet = b"\x00" * CHUNK
        while True:
            await stt.send_audio(quiet)
            await asyncio.sleep(0.1)

    feeder = asyncio.create_task(_silence_feeder())
    try:
        while True:
            evt = await asyncio.wait_for(stt.get_event(), timeout=6.0)
            if isinstance(evt, TranscriptEvent) and evt.text.strip():
                return time.perf_counter() - t_end
    except asyncio.TimeoutError:
        return float("inf")
    finally:
        feeder.cancel()


async def bench_stt_endpoint(n: int = 3) -> None:
    key = _api_key()
    speech = _speech_16k()
    for mode, flush in [("natural endpoint", False), ("forced flush   ", True)]:
        times = []
        for _ in range(n):
            stt = SarvamSTTClient(key, buffer_ms=100)
            await stt.connect(language="en-IN")
            dt = await _stream_utterance(stt, speech, flush_at_end=flush)
            await stt.close()
            times.append(dt * 1000)
            await asyncio.sleep(0.5)
        ok = [t for t in times if t != float("inf")]
        print(f"STT {mode}: {[f'{t:.0f}' for t in times]} ms"
              f"  median={statistics.median(ok):.0f}ms" if ok else f"STT {mode}: all timed out")


REALISTIC_SYSTEM = (
    "You are Ava, the virtual receptionist for Cocolevio LLC, a technology "
    "consulting company in Austin, Texas. Speak as the company (we/our), never "
    "restate the caller's request. Be warm, professional and BRIEF: 1-2 short "
    "sentences unless asked for details.\n\nFACTS (answer from these — never "
    "invent or contradict them):\n- Cocolevio is a technology consulting "
    "company based in Austin, Texas, founded in 2015.\n- Services: custom "
    "software development, cloud migration, AI and machine learning, big data "
    "analytics.\n- Products: CocolevioHR, a recruitment automation product, "
    "and LIMS, a learning management system."
)
HISTORY = [
    {"role": "user", "content": "Hi, can you tell me about Cocolevio?"},
    {"role": "assistant", "content": "Cocolevio is a technology consulting company in Austin, Texas."},
    {"role": "user", "content": "What services do you offer for retail companies?"},
]


async def bench_llm_ttft(n: int = 3) -> None:
    key = _api_key()
    for model in ("sarvam-30b", "sarvam-105b"):
        for label, msgs in [
            ("full prompt ", [{"role": "system", "content": REALISTIC_SYSTEM}] + HISTORY),
            ("slim prompt ", [{"role": "system", "content": REALISTIC_SYSTEM[:220]}] + HISTORY[-1:]),
        ]:
            times = []
            for _ in range(n):
                llm = SarvamLLMClient(key, model=model)
                q: asyncio.Queue = asyncio.Queue()
                t0 = time.perf_counter()
                task = asyncio.create_task(llm.generate_sentences(msgs, q))
                first = None
                while True:
                    evt = await asyncio.wait_for(q.get(), timeout=15.0)
                    if first is None and isinstance(evt, SentenceEvent):
                        first = (time.perf_counter() - t0) * 1000
                    if evt is None:
                        break
                await task
                await llm.close()
                times.append(first or float("inf"))
            print(f"LLM {model} {label}: {[f'{t:.0f}' for t in times]} ms"
                  f"  median={statistics.median(times):.0f}ms")


async def bench_tts_ttfa(n: int = 3) -> None:
    key = _api_key()
    TEXT = "We offer custom software development and cloud migration services."
    for label, kwargs in [
        ("linear16 8k ", {"sample_rate": "8000"}),
    ]:
        times = []
        for _ in range(n):
            tts = SarvamTTSClient(key, model="bulbul:v3")
            await tts.connect(language="en-IN", voice="ishita", **kwargs)
            t0 = time.perf_counter()
            await tts.send_text(TEXT)
            await tts.flush()
            chunk = await asyncio.wait_for(tts.get_audio(), timeout=10.0)
            times.append((time.perf_counter() - t0) * 1000 if chunk else float("inf"))
            await tts.close()
            await asyncio.sleep(0.3)
        print(f"TTS {label}: {[f'{t:.0f}' for t in times]} ms  median={statistics.median(times):.0f}ms")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "stt-endpoint"
    fn = {"stt-endpoint": bench_stt_endpoint, "llm-ttft": bench_llm_ttft,
          "tts-ttfa": bench_tts_ttfa}[which]
    asyncio.run(fn())
