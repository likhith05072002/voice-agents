"""
Pipeline Optimization Benchmark
=================================
Tests multiple approaches head-to-head to find the fastest architecture.

Approach A: Sequential (current) — LLM full sentence -> TTS
Approach B: Parallel pipe — LLM tokens stream into TTS concurrently
Approach C: Early send — Send to TTS after 20 chars (not full sentence)
Approach D: bulbul:v3 — Try faster TTS model

Run: python scripts/optimize_pipeline.py
"""

import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import httpx
import websockets

API_KEY = os.getenv("SARVAM_API_KEY")
LLM_URL = os.getenv("SARVAM_LLM_BASE_URL")

SYS = "జ్యూవెల్లరీ షాప్ అసిస్టెంట్ లక్ష్మి. 1 వాక్యం మాత్రమే చెప్పండి. బంగారం: 7800/gram. షాప్: 10AM-9PM."

TESTS = [
    "bangaram rate enta?",
    "shop timings?",
    "gold necklace?",
    "address cheppandi",
    "bangaram enta?",
]


async def open_tts(model="bulbul:v2", speaker="anushka"):
    ws = await websockets.connect(
        "wss://api.sarvam.ai/text-to-speech/ws",
        additional_headers={"api-subscription-key": API_KEY},
    )
    await ws.send(json.dumps({"type": "config", "data": {
        "target_language_code": "te-IN", "speaker": speaker,
        "model": model, "speech_sample_rate": "8000",
        "send_completion_event": True,
    }}))
    return ws


async def warm_tts(ws):
    await ws.send(json.dumps({"type": "text", "data": {"text": "warm up"}}))
    await ws.send(json.dumps({"type": "flush"}))
    try:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
            if json.loads(raw).get("type") in ("event", "error"):
                break
    except (asyncio.TimeoutError, Exception):
        pass


async def tts_first_audio_time(ws, text):
    """Send text, return ms to first audio chunk."""
    t0 = time.perf_counter()
    await ws.send(json.dumps({"type": "text", "data": {"text": text}}))
    await ws.send(json.dumps({"type": "flush"}))
    try:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            msg = json.loads(raw)
            if msg.get("type") == "audio":
                return (time.perf_counter() - t0) * 1000
            if msg.get("type") in ("event", "error"):
                return -1
    except (asyncio.TimeoutError, Exception):
        return -1


# ============================================================
# APPROACH A: Sequential — LLM full sentence, then TTS
# ============================================================
async def approach_a(http, tts_ws, user_input):
    messages = [{"role": "system", "content": SYS}, {"role": "user", "content": user_input}]
    t0 = time.perf_counter()
    sentence_ends = {".", "?", "!", "\u0964", "\n"}
    buf = ""
    t_first = None

    async with http.stream(
        "POST", f"{LLM_URL}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={"model": "sarvam-30b", "messages": messages, "stream": True,
              "max_tokens": 128, "reasoning_effort": None},
    ) as resp:
        async for line in resp.aiter_lines():
            if not line.startswith("data: "): continue
            d = line[6:].strip()
            if d == "[DONE]": break
            try: chunk = json.loads(d)
            except: continue
            choices = chunk.get("choices", [])
            if not choices: continue
            c = choices[0].get("delta", {}).get("content")
            if not c: continue
            if t_first is None: t_first = time.perf_counter()
            buf += c
            s = buf.rstrip()
            if len(s) >= 15 and s[-1] in sentence_ends:
                break

    if not buf.strip():
        return -1, -1, -1, ""

    t_llm_done = time.perf_counter()
    llm_ms = (t_llm_done - t0) * 1000

    # Now send to TTS
    tts_ttfa = await tts_first_audio_time(tts_ws, buf.strip())
    total = (time.perf_counter() - t0) * 1000 if tts_ttfa > 0 else -1

    return llm_ms, tts_ttfa, total, buf.strip()


# ============================================================
# APPROACH B: Parallel — LLM streams tokens, TTS gets text concurrently
# ============================================================
async def approach_b(http, user_input, tts_model="bulbul:v2", tts_speaker="anushka"):
    """LLM and TTS run concurrently. As LLM produces tokens, we accumulate
    and send to TTS as soon as we have 25+ chars. TTS starts synthesizing
    while LLM continues."""
    messages = [{"role": "system", "content": SYS}, {"role": "user", "content": user_input}]
    t0 = time.perf_counter()

    # Open fresh TTS connection for this approach
    tts_ws = await open_tts(model=tts_model, speaker=tts_speaker)

    buf = ""
    sent_to_tts = False
    t_first_token = None
    t_tts_sent = None
    t_first_audio = None
    tts_recv_task = None

    async def wait_for_tts_audio():
        nonlocal t_first_audio
        try:
            while True:
                raw = await asyncio.wait_for(tts_ws.recv(), timeout=10.0)
                msg = json.loads(raw)
                if msg.get("type") == "audio" and t_first_audio is None:
                    t_first_audio = time.perf_counter()
                    return
                if msg.get("type") in ("event", "error"):
                    return
        except (asyncio.TimeoutError, Exception):
            pass

    async with http.stream(
        "POST", f"{LLM_URL}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={"model": "sarvam-30b", "messages": messages, "stream": True,
              "max_tokens": 128, "reasoning_effort": None},
    ) as resp:
        async for line in resp.aiter_lines():
            if not line.startswith("data: "): continue
            d = line[6:].strip()
            if d == "[DONE]": break
            try: chunk = json.loads(d)
            except: continue
            choices = chunk.get("choices", [])
            if not choices: continue
            c = choices[0].get("delta", {}).get("content")
            if not c: continue
            if t_first_token is None: t_first_token = time.perf_counter()
            buf += c

            # Send to TTS as soon as we have enough text
            if not sent_to_tts and len(buf.strip()) >= 25:
                t_tts_sent = time.perf_counter()
                await tts_ws.send(json.dumps({"type": "text", "data": {"text": buf.strip()}}))
                await tts_ws.send(json.dumps({"type": "flush"}))
                sent_to_tts = True
                # Start listening for TTS audio concurrently
                tts_recv_task = asyncio.create_task(wait_for_tts_audio())

    # If we never sent to TTS, send now
    if not sent_to_tts and buf.strip():
        t_tts_sent = time.perf_counter()
        await tts_ws.send(json.dumps({"type": "text", "data": {"text": buf.strip()}}))
        await tts_ws.send(json.dumps({"type": "flush"}))
        tts_recv_task = asyncio.create_task(wait_for_tts_audio())

    # Wait for first TTS audio
    if tts_recv_task:
        await asyncio.wait_for(tts_recv_task, timeout=10.0)

    await tts_ws.close()

    if t_first_audio is None or t_first_token is None:
        return -1, -1, -1, ""

    llm_ttft = (t_first_token - t0) * 1000
    tts_ttfa = (t_first_audio - t_tts_sent) * 1000 if t_tts_sent else -1
    total = (t_first_audio - t0) * 1000

    return llm_ttft, tts_ttfa, total, buf.strip()


async def main():
    print("=" * 115)
    print("PIPELINE OPTIMIZATION BENCHMARK")
    print("=" * 115)

    async with httpx.AsyncClient(timeout=30.0) as http:

        # --- Approach A: Sequential ---
        print()
        print("APPROACH A: Sequential (LLM full sentence -> TTS)")
        print("-" * 115)
        tts_a = await open_tts()
        await warm_tts(tts_a)

        totals_a = []
        for inp in TESTS:
            llm, tts, total, text = await approach_a(http, tts_a, inp)
            if total > 0: totals_a.append(total)
            t = text.replace("\n", " ")[:50]
            print(f"  {inp:<25} LLM={llm:>5.0f}ms TTS={tts:>5.0f}ms TOTAL={total:>5.0f}ms  {t}")
        await tts_a.close()

        if totals_a:
            print(f"  AVG: {sum(totals_a)/len(totals_a):.0f}ms | MIN: {min(totals_a):.0f}ms | MAX: {max(totals_a):.0f}ms")

        # --- Approach B: Parallel (bulbul:v2) ---
        print()
        print("APPROACH B: Parallel pipe, 25-char trigger (bulbul:v2)")
        print("-" * 115)

        totals_b = []
        for inp in TESTS:
            llm, tts, total, text = await approach_b(http, inp, "bulbul:v2", "anushka")
            if total > 0: totals_b.append(total)
            t = text.replace("\n", " ")[:50]
            print(f"  {inp:<25} LLM={llm:>5.0f}ms TTS={tts:>5.0f}ms TOTAL={total:>5.0f}ms  {t}")

        if totals_b:
            print(f"  AVG: {sum(totals_b)/len(totals_b):.0f}ms | MIN: {min(totals_b):.0f}ms | MAX: {max(totals_b):.0f}ms")

        # --- Approach C: Parallel (bulbul:v3) ---
        print()
        print("APPROACH C: Parallel pipe, 25-char trigger (bulbul:v3, voice=shubh)")
        print("-" * 115)

        totals_c = []
        for inp in TESTS:
            llm, tts, total, text = await approach_b(http, inp, "bulbul:v3", "shubh")
            if total > 0: totals_c.append(total)
            t = text.replace("\n", " ")[:50]
            print(f"  {inp:<25} LLM={llm:>5.0f}ms TTS={tts:>5.0f}ms TOTAL={total:>5.0f}ms  {t}")

        if totals_c:
            print(f"  AVG: {sum(totals_c)/len(totals_c):.0f}ms | MIN: {min(totals_c):.0f}ms | MAX: {max(totals_c):.0f}ms")

    # --- Final comparison ---
    print()
    print("=" * 115)
    print("COMPARISON:")
    print(f"  A (Sequential, v2):     avg={sum(totals_a)/len(totals_a):.0f}ms" if totals_a else "  A: NO DATA")
    print(f"  B (Parallel, v2):       avg={sum(totals_b)/len(totals_b):.0f}ms" if totals_b else "  B: NO DATA")
    print(f"  C (Parallel, v3):       avg={sum(totals_c)/len(totals_c):.0f}ms" if totals_c else "  C: NO DATA")

    best = []
    if totals_a: best.append(("A-Sequential-v2", sum(totals_a)/len(totals_a)))
    if totals_b: best.append(("B-Parallel-v2", sum(totals_b)/len(totals_b)))
    if totals_c: best.append(("C-Parallel-v3", sum(totals_c)/len(totals_c)))

    if best:
        best.sort(key=lambda x: x[1])
        winner = best[0]
        print()
        print(f"  WINNER: {winner[0]} at {winner[1]:.0f}ms avg")
        if winner[1] < 600:
            print(f"  TARGET MET! {winner[1]:.0f}ms < 600ms")
        elif winner[1] < 700:
            print(f"  ACCEPTABLE! {winner[1]:.0f}ms < 700ms")
        else:
            print(f"  {winner[1]:.0f}ms — further optimization needed")


if __name__ == "__main__":
    asyncio.run(main())
