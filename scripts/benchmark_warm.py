"""Warm-connection pipeline benchmark — simulates production scenario."""
import asyncio
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

SYS = "జ్యూవెల్లరీ షాప్ అసిస్టెంట్. 1 వాక్యం మాత్రమే. బంగారం: 7800/gram. షాప్: 10AM-9PM."

TESTS = [
    "bangaram rate enta?",
    "shop timings?",
    "gold necklace chupinchandi",
    "address cheppandi",
    "bangaram enta eeroju?",
    "gold rate today?",
    "shop ela veltham?",
    "rate cheppandi andi",
    "eeroju bangaram dhara?",
    "bangaram rate?",
]


async def llm_stream_to_20chars(http, user_input):
    """Stream LLM until we have 20+ content chars. Return text + timing."""
    messages = [{"role": "system", "content": SYS}, {"role": "user", "content": user_input}]
    buf = ""
    t_start = time.perf_counter()
    t_first_token = None

    try:
        async with http.stream(
            "POST", f"{LLM_URL}/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={"model": "sarvam-30b", "messages": messages, "stream": True,
                  "max_tokens": 128, "reasoning_effort": None},
        ) as resp:
            if resp.status_code != 200:
                return "", -1, -1

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                d = line[6:].strip()
                if d == "[DONE]":
                    break
                try:
                    chunk = json.loads(d)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                c = delta.get("content")
                if not c:
                    continue

                if t_first_token is None:
                    t_first_token = time.perf_counter()
                buf += c

                if len(buf.strip()) >= 20:
                    break
    except Exception:
        return "", -1, -1

    ttft = (t_first_token - t_start) * 1000 if t_first_token else -1
    t20 = (time.perf_counter() - t_start) * 1000
    return buf, ttft, t20


async def tts_first_audio(tts_ws, text):
    """Send text to pre-connected TTS and measure time to first audio."""
    t_start = time.perf_counter()
    await tts_ws.send(json.dumps({"type": "text", "data": {"text": text}}))
    await tts_ws.send(json.dumps({"type": "flush"}))

    try:
        while True:
            raw = await asyncio.wait_for(tts_ws.recv(), timeout=5.0)
            msg = json.loads(raw)
            if msg.get("type") == "audio":
                return (time.perf_counter() - t_start) * 1000
            if msg.get("type") in ("event", "error"):
                # Drain any remaining audio after event
                break
    except (asyncio.TimeoutError, Exception):
        pass
    return -1


async def main():
    # 1. Open persistent TTS connection
    tts_ws = await websockets.connect(
        "wss://api.sarvam.ai/text-to-speech/ws",
        additional_headers={"api-subscription-key": API_KEY},
    )
    await tts_ws.send(json.dumps({"type": "config", "data": {
        "target_language_code": "te-IN", "speaker": "anushka", "model": "bulbul:v2",
        "speech_sample_rate": "8000", "output_audio_codec": "mulaw",
        "pace": 1.0, "min_buffer_size": 30, "max_chunk_length": 150,
        "text_aggregation_mode": "SENTENCE", "send_completion_event": True,
    }}))

    # 2. Warm up TTS
    await tts_ws.send(json.dumps({"type": "text", "data": {"text": "నమస్కారం, వార్మ్ అప్"}}))
    await tts_ws.send(json.dumps({"type": "flush"}))
    try:
        while True:
            raw = await asyncio.wait_for(tts_ws.recv(), timeout=3.0)
            if json.loads(raw).get("type") in ("event", "error"):
                break
    except Exception:
        pass

    print("PRODUCTION SCENARIO: Warm TTS + LLM (20-char early send)")
    print("=" * 115)
    print(f"{'Input':<28} {'LLM TTFT':>9} {'LLM 20ch':>9} {'TTS TTFA':>9} {'TOTAL':>8}  Text")
    print("-" * 115)

    totals = []
    llm_ttfts = []
    tts_ttfas = []

    async with httpx.AsyncClient(timeout=30.0) as http:
        for inp in TESTS:
            text, llm_ttft, llm_20ch = await llm_stream_to_20chars(http, inp)
            if not text.strip():
                print(f"{inp:<28} ERROR: empty LLM")
                continue

            tts_ttfa = await tts_first_audio(tts_ws, text)
            if tts_ttfa < 0:
                # TTS connection might have died, reconnect
                try:
                    await tts_ws.close()
                except Exception:
                    pass
                tts_ws = await websockets.connect(
                    "wss://api.sarvam.ai/text-to-speech/ws",
                    additional_headers={"api-subscription-key": API_KEY},
                )
                await tts_ws.send(json.dumps({"type": "config", "data": {
                    "target_language_code": "te-IN", "speaker": "anushka", "model": "bulbul:v2",
                    "speech_sample_rate": "8000", "output_audio_codec": "mulaw",
                    "pace": 1.0, "min_buffer_size": 30, "max_chunk_length": 150,
                    "text_aggregation_mode": "SENTENCE", "send_completion_event": True,
                }}))
                print(f"{inp:<28} TTS reconnected, retrying...")
                continue

            total = llm_20ch + tts_ttfa
            totals.append(total)
            llm_ttfts.append(llm_ttft)
            tts_ttfas.append(tts_ttfa)

            t = text.replace("\n", " ")[:50]
            print(f"{inp:<28} {llm_ttft:>8.0f}ms {llm_20ch:>8.0f}ms {tts_ttfa:>8.0f}ms {total:>7.0f}ms  {t}")

            # Small delay between tests to not hammer API
            await asyncio.sleep(0.5)

    await tts_ws.close()

    if totals:
        avg = sum(totals) / len(totals)
        s = sorted(totals)
        p50 = s[len(s) // 2]
        p95 = s[min(int(len(s) * 0.95), len(s) - 1)]
        avg_llm = sum(llm_ttfts) / len(llm_ttfts)
        avg_tts = sum(tts_ttfas) / len(tts_ttfas)

        print()
        print("=" * 115)
        print(f"RESULTS ({len(totals)} tests):")
        print(f"  LLM TTFT:    avg={avg_llm:.0f}ms  min={min(llm_ttfts):.0f}ms  max={max(llm_ttfts):.0f}ms")
        print(f"  TTS TTFA:    avg={avg_tts:.0f}ms  min={min(tts_ttfas):.0f}ms  max={max(tts_ttfas):.0f}ms")
        print(f"  TOTAL:       avg={avg:.0f}ms  min={min(totals):.0f}ms  max={max(totals):.0f}ms  P50={p50:.0f}ms  P95={p95:.0f}ms")
        print()

        if avg < 600:
            verdict = f"TARGET MET: {avg:.0f}ms avg"
        elif avg < 700:
            verdict = f"ACCEPTABLE: {avg:.0f}ms avg (under 700ms max)"
        elif avg < 900:
            verdict = f"CLOSE: {avg:.0f}ms avg. With 250ms filler, perceived ~250ms."
        else:
            verdict = f"NEEDS WORK: {avg:.0f}ms avg."

        print(f"  {verdict}")
        print()
        print(f"  PRODUCTION PERCEIVED LATENCY:")
        print(f"    Turn detection:     ~250ms")
        print(f"    Filler plays at:    ~250ms (immediate after turn detect)")
        print(f"    Real TTS arrives:   ~{avg:.0f}ms after turn detect")
        print(f"    Filler masks:       ~{avg - 250:.0f}ms of dead time")
        print(f"    User perception:    CONTINUOUS AUDIO from ~250ms")


if __name__ == "__main__":
    asyncio.run(main())
