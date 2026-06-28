"""
Optimization V2: Focus on what actually reduces latency.
==========================================================
1. Ultra-short system prompt (fewer input tokens)
2. max_tokens=64 (less generation)
3. Warm TTS with proper drain between requests
4. Test bulbul:v3 voices that work with Telugu

Run: python scripts/optimize_v2.py
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

# System prompts: short vs original
PROMPT_SHORT = "జ్యూవెల్లరీ అసిస్టెంట్. 1 వాక్యం. Gold:7800/g. Shop:10AM-9PM. Addr:Banjara Hills."
PROMPT_MEDIUM = (
    "మీరు నమ శ్రీనివాస జ్యూవెల్లరీ అసిస్టెంట్ లక్ష్మి. "
    "1-2 వాక్యాలు. బంగారం:7800/gram. షాప్:10AM-9PM. బంజారా హిల్స్."
)

TESTS = [
    "bangaram rate?",
    "shop timings?",
    "necklace chupinchandi",
    "address enti?",
    "gold enta?",
    "pata bangaram exchange?",
    "making charges?",
    "timings?",
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


async def warm_and_drain(ws, text="warming up test audio"):
    """Warm TTS and fully drain the response."""
    await ws.send(json.dumps({"type": "text", "data": {"text": text}}))
    await ws.send(json.dumps({"type": "flush"}))
    while True:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            if json.loads(raw).get("type") in ("event", "error"):
                break
        except (asyncio.TimeoutError, Exception):
            break


async def run_test(http, tts_ws, prompt, max_tokens, user_input):
    """LLM sentence -> TTS first audio. Returns total ms."""
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": user_input}]
    sentence_ends = {".", "?", "!", "\u0964", "\n"}
    t0 = time.perf_counter()
    buf = ""
    t_first = None

    async with http.stream(
        "POST", f"{LLM_URL}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={"model": "sarvam-30b", "messages": messages, "stream": True,
              "max_tokens": max_tokens, "reasoning_effort": None},
    ) as resp:
        if resp.status_code != 200:
            return -1, -1, -1, ""
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
            if len(s) >= 12 and s[-1] in sentence_ends:
                break

    if not buf.strip():
        return -1, -1, -1, ""

    llm_ms = (time.perf_counter() - t0) * 1000

    # TTS
    t_tts = time.perf_counter()
    await tts_ws.send(json.dumps({"type": "text", "data": {"text": buf.strip()}}))
    await tts_ws.send(json.dumps({"type": "flush"}))

    tts_ttfa = -1
    while True:
        try:
            raw = await asyncio.wait_for(tts_ws.recv(), timeout=5.0)
            msg = json.loads(raw)
            if msg.get("type") == "audio":
                if tts_ttfa < 0:
                    tts_ttfa = (time.perf_counter() - t_tts) * 1000
            elif msg.get("type") in ("event", "error"):
                break
        except (asyncio.TimeoutError, Exception):
            break

    total = llm_ms + tts_ttfa if tts_ttfa > 0 else -1
    return llm_ms, tts_ttfa, total, buf.strip()


async def main():
    print("=" * 115)
    print("OPTIMIZATION V2 — Finding the fastest configuration")
    print("=" * 115)

    configs = [
        ("Short prompt, max_tokens=64, v2", PROMPT_SHORT, 64, "bulbul:v2", "anushka"),
        ("Medium prompt, max_tokens=128, v2", PROMPT_MEDIUM, 128, "bulbul:v2", "anushka"),
        ("Short prompt, max_tokens=64, v3", PROMPT_SHORT, 64, "bulbul:v3", "anushka"),
        ("Medium prompt, max_tokens=128, v3", PROMPT_MEDIUM, 128, "bulbul:v3", "anushka"),
    ]

    results = {}

    async with httpx.AsyncClient(timeout=30.0) as http:
        for config_name, prompt, max_tok, tts_model, speaker in configs:
            print(f"\n--- {config_name} ---")

            try:
                tts_ws = await open_tts(model=tts_model, speaker=speaker)
            except Exception as e:
                print(f"  TTS connection failed: {e}")
                continue

            try:
                await warm_and_drain(tts_ws)
            except Exception:
                print(f"  TTS warmup failed, skipping")
                await tts_ws.close()
                continue

            totals = []
            for inp in TESTS:
                llm, tts, total, text = await run_test(http, tts_ws, prompt, max_tok, inp)
                if total > 0:
                    totals.append(total)
                t = text.replace("\n", " ")[:45]
                status = "OK" if total > 0 else "FAIL"
                if total > 0:
                    print(f"  {inp:<22} LLM={llm:>4.0f} TTS={tts:>4.0f} TOT={total:>4.0f}ms  {t}")
                else:
                    print(f"  {inp:<22} FAILED")

            await tts_ws.close()

            if totals:
                avg = sum(totals) / len(totals)
                results[config_name] = avg
                print(f"  >> AVG: {avg:.0f}ms | MIN: {min(totals):.0f}ms | MAX: {max(totals):.0f}ms")

    # Final comparison
    print()
    print("=" * 115)
    print("RESULTS COMPARISON:")
    for name, avg in sorted(results.items(), key=lambda x: x[1]):
        marker = " << BEST" if avg == min(results.values()) else ""
        target = " TARGET MET!" if avg < 600 else (" ACCEPTABLE" if avg < 700 else "")
        print(f"  {avg:>6.0f}ms  {name}{marker}{target}")


if __name__ == "__main__":
    asyncio.run(main())
