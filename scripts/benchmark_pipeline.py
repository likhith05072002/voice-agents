"""
End-to-End Pipeline Latency Benchmark
======================================
Measures the actual time from text input (simulating STT output)
through LLM streaming to first TTS audio byte.

This is the critical path: STT_final -> LLM -> TTS -> first_audio

Run: python scripts/benchmark_pipeline.py
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import httpx

from src.services.llm.sarvam import SarvamLLMClient, SentenceEvent
from src.services.tts.sarvam import SarvamTTSClient

API_KEY = os.getenv("SARVAM_API_KEY")

SYSTEM_PROMPT = (
    "మీరు నమ శ్రీనివాస జ్యూవెల్లరీ షాప్ AI అసిస్టెంట్ లక్ష్మి. "
    "మర్యాదగా మాట్లాడండి. సమాధానాలు చిన్నగా ఉంచండి (1-2 వాక్యాలు). "
    "షాప్ సమయాలు: ఉదయం 10 - రాత్రి 9. "
    "ఈ రోజు బంగారం ధర: గ్రాముకు 7,800 రూపాయలు."
)

TEST_INPUTS = [
    ("TE: gold rate", "andi eeroju bangaram rate enta?"),
    ("TE: appointment", "repu ravachchu kadha shop ki?"),
    ("TE: necklace", "oka gold necklace chupinchandi"),
    ("HI: gold rate", "aaj sone ka bhav kya hai?"),
    ("EN: timing", "what are your shop timings?"),
]


async def measure_pipeline(llm: SarvamLLMClient, tts: SarvamTTSClient, name: str, user_input: str) -> dict:
    """Measure: user_input -> LLM streaming -> first sentence -> TTS -> first audio byte."""

    # Reconnect TTS if connection dropped
    if not tts.is_connected:
        await tts.connect(language="te-IN", voice="anushka", sample_rate="8000")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_input},
    ]

    sentence_queue = asyncio.Queue()
    t_start = time.perf_counter()

    # Start LLM generation
    llm_task = asyncio.create_task(llm.generate_sentences(messages, sentence_queue))

    # Wait for first sentence from LLM
    first_sentence = None
    t_first_sentence = None
    while True:
        evt = await sentence_queue.get()
        if evt is None:
            break
        if isinstance(evt, SentenceEvent):
            first_sentence = evt.text
            t_first_sentence = time.perf_counter()
            break

    if not first_sentence:
        llm_task.cancel()
        return {"name": name, "error": "No sentence from LLM"}

    llm_ttft_ms = (t_first_sentence - t_start) * 1000

    # Send first sentence to TTS
    await tts.reset()
    t_tts_send = time.perf_counter()
    await tts.send_text(first_sentence)
    await tts.flush()

    # Wait for first audio chunk
    first_audio = await tts.get_audio()
    t_first_audio = time.perf_counter()

    if first_audio is None:
        llm_task.cancel()
        return {"name": name, "error": "No audio from TTS"}

    tts_ttfa_ms = (t_first_audio - t_tts_send) * 1000
    total_ms = (t_first_audio - t_start) * 1000
    audio_size = len(first_audio)

    # Cancel remaining LLM generation (we only needed first sentence for this benchmark)
    llm.cancel()
    try:
        await asyncio.wait_for(llm_task, timeout=2.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass

    # Drain remaining TTS audio (with timeout protection)
    try:
        while True:
            chunk = await asyncio.wait_for(tts.get_audio(), timeout=3.0)
            if chunk is None:
                break
    except (asyncio.TimeoutError, Exception):
        pass  # Drain timed out, acceptable for benchmark

    return {
        "name": name,
        "llm_first_sentence_ms": round(llm_ttft_ms),
        "tts_ttfa_ms": round(tts_ttfa_ms),
        "total_ms": round(total_ms),
        "audio_bytes": audio_size,
        "sentence": first_sentence.replace("\n", " ")[:80],
    }


async def main():
    print("=" * 110)
    print("PIPELINE LATENCY BENCHMARK: LLM first sentence -> TTS first audio")
    print("This measures the CRITICAL PATH from user input to first audio byte")
    print("=" * 110)
    print()

    llm = SarvamLLMClient(api_key=API_KEY, model="sarvam-30b")
    tts = SarvamTTSClient(api_key=API_KEY, model="bulbul:v2")

    # Connect TTS once (persistent connection)
    await tts.connect(language="te-IN", voice="anushka", sample_rate="8000")

    print(f"{'Test':<20} {'LLM 1st':>8} {'TTS TTFA':>9} {'TOTAL':>8}  Sentence")
    print("-" * 110)

    totals = []

    for name, user_input in TEST_INPUTS:
        result = await measure_pipeline(llm, tts, name, user_input)

        if "error" in result:
            print(f"{name:<20} ERROR: {result['error']}")
            continue

        totals.append(result["total_ms"])
        print(
            f"{result['name']:<20} "
            f"{result['llm_first_sentence_ms']:>7}ms "
            f"{result['tts_ttfa_ms']:>8}ms "
            f"{result['total_ms']:>7}ms  "
            f"{result['sentence']}"
        )

    # Run the first test 3 more times for consistency check
    print()
    print("--- Consistency check (repeat Telugu gold rate 3x) ---")
    for i in range(3):
        result = await measure_pipeline(llm, tts, f"TE: gold #{i+1}", "bangaram rate enta?")
        if "error" not in result:
            totals.append(result["total_ms"])
            print(
                f"{result['name']:<20} "
                f"{result['llm_first_sentence_ms']:>7}ms "
                f"{result['tts_ttfa_ms']:>8}ms "
                f"{result['total_ms']:>7}ms  "
                f"{result['sentence']}"
            )

    await tts.close()
    await llm.close()

    # Summary
    print()
    print("=" * 110)
    if totals:
        avg = sum(totals) / len(totals)
        mn = min(totals)
        mx = max(totals)
        sorted_t = sorted(totals)
        p50 = sorted_t[len(sorted_t) // 2]
        p95 = sorted_t[min(int(len(sorted_t) * 0.95), len(sorted_t) - 1)]

        print(f"LLM->TTS PIPELINE (text in -> first audio byte out):")
        print(f"  Avg:  {avg:.0f}ms")
        print(f"  Min:  {mn:.0f}ms")
        print(f"  Max:  {mx:.0f}ms")
        print(f"  P50:  {p50:.0f}ms")
        print(f"  P95:  {p95:.0f}ms")
        print()
        print(f"FULL VOICE PIPELINE ESTIMATE (add STT + turn detection):")
        print(f"  This benchmark:           {avg:.0f}ms (LLM first sentence + TTS TTFA)")
        print(f"  + Turn detection:         ~0ms   (filler plays immediately)")
        print(f"  = Perceived first audio:  ~250ms (filler)")
        print(f"  = Real first TTS audio:   ~{avg:.0f}ms after turn detect")
        print()

        if avg < 600:
            print(f"  >> TARGET MET: {avg:.0f}ms < 600ms. First audio under target!")
        elif avg < 700:
            print(f"  >> CLOSE: {avg:.0f}ms < 700ms. Within acceptable range.")
        else:
            print(f"  >> NEEDS OPTIMIZATION: {avg:.0f}ms > 700ms.")
            print(f"     Bottleneck analysis needed.")


async def measure_direct_pipe(name: str, user_input: str) -> dict:
    """Alternative: pipe LLM tokens DIRECTLY to TTS without sentence buffering.
    LLM streams tokens -> each token sent to TTS -> TTS aggregates with TOKEN mode.
    This removes sentence accumulation delay entirely."""

    llm_client = httpx.AsyncClient(timeout=30.0)
    tts = SarvamTTSClient(api_key=API_KEY, model="bulbul:v2")
    await tts.connect(language="te-IN", voice="anushka", sample_rate="8000")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_input},
    ]

    t_start = time.perf_counter()
    t_first_audio = None
    first_llm_token_time = None
    full_content = ""

    # Start LLM streaming and pipe tokens directly to TTS
    async with llm_client.stream(
        "POST",
        f"{os.getenv('SARVAM_LLM_BASE_URL')}/chat/completions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "sarvam-30b",
            "messages": messages,
            "stream": True,
            "max_tokens": 256,
            "reasoning_effort": None,
        },
    ) as resp:
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            data_str = line[6:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            choices = chunk.get("choices", [])
            if not choices:
                continue
            content = choices[0].get("delta", {}).get("content")
            if not content:
                continue

            if first_llm_token_time is None:
                first_llm_token_time = time.perf_counter()

            full_content += content
            # Pipe directly to TTS (filter empty/whitespace-only tokens)
            if content.strip():
                await tts.send_text(content)

            # Check if TTS has produced audio yet
            if t_first_audio is None:
                try:
                    audio = await asyncio.wait_for(tts.get_audio(), timeout=0.01)
                    if audio is not None:
                        t_first_audio = time.perf_counter()
                except asyncio.TimeoutError:
                    pass

    # Flush TTS and wait for first audio if we haven't got it yet
    await tts.flush()
    if t_first_audio is None:
        try:
            audio = await asyncio.wait_for(tts.get_audio(), timeout=5.0)
            if audio is not None:
                t_first_audio = time.perf_counter()
        except asyncio.TimeoutError:
            pass

    await tts.close()
    await llm_client.aclose()

    if t_first_audio is None or first_llm_token_time is None:
        return {"name": name, "error": "No audio or no LLM tokens"}

    return {
        "name": name,
        "llm_ttft_ms": round((first_llm_token_time - t_start) * 1000),
        "total_ms": round((t_first_audio - t_start) * 1000),
        "sentence": full_content.replace("\n", " ")[:80],
    }


async def main2():
    """Benchmark: Direct LLM->TTS piping (no sentence buffering)."""
    print()
    print("=" * 110)
    print("ALTERNATIVE: DIRECT LLM->TTS PIPE (no sentence buffering)")
    print("LLM tokens stream directly into TTS TOKEN mode")
    print("=" * 110)
    print()
    print(f"{'Test':<20} {'LLM TTFT':>9} {'TOTAL':>8}  Response")
    print("-" * 110)

    totals = []
    for name, user_input in TEST_INPUTS:
        result = await measure_direct_pipe(name, user_input)
        if "error" in result:
            print(f"{name:<20} ERROR: {result['error']}")
            continue
        totals.append(result["total_ms"])
        print(
            f"{result['name']:<20} "
            f"{result['llm_ttft_ms']:>8}ms "
            f"{result['total_ms']:>7}ms  "
            f"{result['sentence']}"
        )

    if totals:
        avg = sum(totals) / len(totals)
        print()
        print(f"Direct pipe avg: {avg:.0f}ms (vs sentence-buffered approach above)")
        if avg < 600:
            print(f">> TARGET MET: {avg:.0f}ms < 600ms!")
        elif avg < 700:
            print(f">> ACCEPTABLE: {avg:.0f}ms < 700ms")
        else:
            print(f">> NEEDS WORK: {avg:.0f}ms > 700ms")


async def run_all():
    await main()
    await main2()


if __name__ == "__main__":
    asyncio.run(run_all())
