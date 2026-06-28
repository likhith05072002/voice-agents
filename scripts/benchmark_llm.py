"""
Sarvam-30B LLM Benchmark for Voice Agent
=========================================
Tests TTFT (time to first token), ITL (inter-token latency),
and response quality across languages and context lengths.

Run: python scripts/benchmark_llm.py

Decision gate:
  < 500ms TTFT  -> Excellent, minimal fillers needed
  500-800ms     -> Good, standard fillers (300ms)
  800-1500ms    -> Acceptable, extended fillers (500-800ms)
  > 1500ms      -> Need hybrid approach (Groq for English)
"""

import asyncio
import time
import sys
import os
import json
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

import httpx

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")
SARVAM_LLM_BASE_URL = os.getenv("SARVAM_LLM_BASE_URL", "https://api.sarvam.ai/v1")
SARVAM_LLM_MODEL = os.getenv("SARVAM_LLM_MODEL", "sarvam-m4")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# --- Test Prompts ---

SYSTEM_PROMPT_TELUGU = """మీరు డాక్టర్ రెడ్డి డెంటల్ క్లినిక్ యొక్క AI రిసెప్షనిస్ట్ "లక్ష్మి".
మర్యాదగా, స్నేహపూర్వకంగా మాట్లాడండి. సమాధానాలు చిన్నగా ఉంచండి (2-3 వాక్యాలు).
క్లినిక్ సమయాలు: ఉదయం 9 - సాయంత్రం 6, సోమ-శని.
డాక్టర్: డా. సుధీర్ రెడ్డి. సేవలు: దంత పరీక్ష, ఫిల్లింగ్, రూట్ కెనాల్, బ్రేసెస్."""

SYSTEM_PROMPT_HINDI = """आप डॉ. रेड्डी डेंटल क्लीनिक की AI रिसेप्शनिस्ट "लक्ष्मी" हैं।
विनम्र और मैत्रीपूर्ण रहें। जवाब छोटे रखें (2-3 वाक्य)।
क्लीनिक का समय: सुबह 9 - शाम 6, सोम-शनि।
डॉक्टर: डॉ. सुधीर रेड्डी। सेवाएं: दंत जांच, फिलिंग, रूट कैनाल, ब्रेसेज।"""

SYSTEM_PROMPT_ENGLISH = """You are "Lakshmi", the AI receptionist for Dr. Reddy's Dental Clinic.
Be polite and friendly. Keep answers short (2-3 sentences).
Clinic hours: 9 AM - 6 PM, Mon-Sat.
Doctor: Dr. Sudheer Reddy. Services: dental checkup, filling, root canal, braces."""

TEST_CASES = [
    # (language, system_prompt, user_message, description)
    ("Telugu", SYSTEM_PROMPT_TELUGU, "నమస్కారం, రేపు అపాయింట్‌మెంట్ కావాలి", "Telugu greeting + appointment"),
    ("Telugu", SYSTEM_PROMPT_TELUGU, "డాక్టర్ గారి ఫీజు ఎంత?", "Telugu pricing query"),
    ("Telugu", SYSTEM_PROMPT_TELUGU, "నాకు పళ్ళు నొప్పిగా ఉన్నాయి, ఏం చేయాలి?", "Telugu pain complaint"),
    ("Hindi", SYSTEM_PROMPT_HINDI, "नमस्ते, कल शाम 4 बजे अपॉइंटमेंट चाहिए", "Hindi appointment"),
    ("Hindi", SYSTEM_PROMPT_HINDI, "डॉक्टर साहब की फीस कितनी है?", "Hindi pricing query"),
    ("English", SYSTEM_PROMPT_ENGLISH, "Hi, I need to book an appointment for tomorrow", "English appointment"),
    ("English", SYSTEM_PROMPT_ENGLISH, "What are your clinic hours?", "English hours query"),
]

MULTI_TURN_HISTORY = [
    {"role": "user", "content": "నమస్కారం"},
    {"role": "assistant", "content": "నమస్కారం! డాక్టర్ రెడ్డి డెంటల్ క్లినిక్‌కి స్వాగతం. మీకు ఏమి సహాయం చేయగలను?"},
    {"role": "user", "content": "రేపు అపాయింట్‌మెంట్ కావాలి"},
    {"role": "assistant", "content": "తప్పనిసరిగా! రేపు ఏ సమయం మీకు అనుకూలం?"},
]


async def benchmark_sarvam(
    client: httpx.AsyncClient,
    system_prompt: str,
    messages: list[dict],
    model: str = None,
) -> dict:
    """Benchmark a single LLM call. Returns timing metrics."""
    model = model or SARVAM_LLM_MODEL

    full_messages = [{"role": "system", "content": system_prompt}] + messages

    payload = {
        "model": model,
        "messages": full_messages,
        "stream": True,
        "max_tokens": 200,
    }

    headers = {
        "Authorization": f"Bearer {SARVAM_API_KEY}",
        "Content-Type": "application/json",
    }

    tokens = []
    token_times = []
    start_time = time.perf_counter()
    ttft = None
    full_response = ""

    try:
        async with client.stream(
            "POST",
            f"{SARVAM_LLM_BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
            timeout=30.0,
        ) as response:
            if response.status_code != 200:
                body = await response.aread()
                return {
                    "error": f"HTTP {response.status_code}: {body.decode()[:200]}",
                    "ttft_ms": -1,
                }

            async for line in response.aiter_lines():
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

                delta = choices[0].get("delta", {})
                content = delta.get("content", "")

                if content:
                    now = time.perf_counter()
                    if ttft is None:
                        ttft = (now - start_time) * 1000  # ms

                    tokens.append(content)
                    token_times.append(now)
                    full_response += content

    except httpx.ReadTimeout:
        return {"error": "Timeout (30s)", "ttft_ms": -1}
    except Exception as e:
        return {"error": str(e), "ttft_ms": -1}

    end_time = time.perf_counter()
    total_time = (end_time - start_time) * 1000

    # Calculate inter-token latencies
    itl_values = []
    for i in range(1, len(token_times)):
        itl_values.append((token_times[i] - token_times[i - 1]) * 1000)

    avg_itl = sum(itl_values) / len(itl_values) if itl_values else 0

    # Estimate first sentence time
    first_sentence_time = None
    accumulated = ""
    for i, token in enumerate(tokens):
        accumulated += token
        if any(accumulated.rstrip().endswith(p) for p in [".", "?", "!", "।", "\n"]):
            if len(accumulated) > 15:  # Minimum sentence length
                first_sentence_time = (token_times[i] - start_time) * 1000
                break

    return {
        "ttft_ms": round(ttft, 1) if ttft else -1,
        "total_ms": round(total_time, 1),
        "tokens": len(tokens),
        "avg_itl_ms": round(avg_itl, 1),
        "first_sentence_ms": round(first_sentence_time, 1) if first_sentence_time else -1,
        "response": full_response[:150],
        "input_tokens_est": len(json.dumps(full_messages)) // 4,  # rough estimate
    }


async def benchmark_openrouter(
    client: httpx.AsyncClient,
    system_prompt: str,
    messages: list[dict],
    model: str = "meta-llama/llama-3.3-70b-instruct",
) -> dict:
    """Benchmark OpenRouter (Groq/Llama) for comparison."""
    if not OPENROUTER_API_KEY:
        return {"error": "No OpenRouter API key", "ttft_ms": -1}

    full_messages = [{"role": "system", "content": system_prompt}] + messages

    payload = {
        "model": model,
        "messages": full_messages,
        "stream": True,
        "max_tokens": 200,
    }

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    tokens = []
    token_times = []
    start_time = time.perf_counter()
    ttft = None
    full_response = ""

    try:
        async with client.stream(
            "POST",
            "https://openrouter.ai/api/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=30.0,
        ) as response:
            if response.status_code != 200:
                body = await response.aread()
                return {
                    "error": f"HTTP {response.status_code}: {body.decode()[:200]}",
                    "ttft_ms": -1,
                }

            async for line in response.aiter_lines():
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

                delta = choices[0].get("delta", {})
                content = delta.get("content", "")

                if content:
                    now = time.perf_counter()
                    if ttft is None:
                        ttft = (now - start_time) * 1000

                    tokens.append(content)
                    token_times.append(now)
                    full_response += content

    except httpx.ReadTimeout:
        return {"error": "Timeout (30s)", "ttft_ms": -1}
    except Exception as e:
        return {"error": str(e), "ttft_ms": -1}

    end_time = time.perf_counter()
    total_time = (end_time - start_time) * 1000

    itl_values = []
    for i in range(1, len(token_times)):
        itl_values.append((token_times[i] - token_times[i - 1]) * 1000)

    avg_itl = sum(itl_values) / len(itl_values) if itl_values else 0

    return {
        "ttft_ms": round(ttft, 1) if ttft else -1,
        "total_ms": round(total_time, 1),
        "tokens": len(tokens),
        "avg_itl_ms": round(avg_itl, 1),
        "response": full_response[:150],
    }


async def run_benchmarks():
    print("=" * 80)
    print("VOICE AGENT LLM BENCHMARK")
    print("=" * 80)
    print(f"\nSarvam Model: {SARVAM_LLM_MODEL}")
    print(f"Sarvam URL:   {SARVAM_LLM_BASE_URL}")
    print(f"OpenRouter:   {'configured' if OPENROUTER_API_KEY else 'not configured'}")
    print()

    async with httpx.AsyncClient() as client:
        # --- Part 1: Single-turn tests across languages ---
        print("-" * 80)
        print("PART 1: Single-Turn Tests (Sarvam-30B)")
        print("-" * 80)
        print(f"{'Test':<35} {'TTFT':>8} {'1st Sent':>8} {'Total':>8} {'Tokens':>6} {'ITL':>6}")
        print("-" * 80)

        sarvam_ttfts = []

        for lang, sys_prompt, user_msg, desc in TEST_CASES:
            messages = [{"role": "user", "content": user_msg}]
            result = await benchmark_sarvam(client, sys_prompt, messages)

            if "error" in result:
                print(f"{desc:<35} ERROR: {result['error'][:40]}")
                continue

            ttft = result["ttft_ms"]
            sarvam_ttfts.append(ttft)
            first_sent = result["first_sentence_ms"]
            total = result["total_ms"]
            tokens = result["tokens"]
            itl = result["avg_itl_ms"]

            print(
                f"{desc:<35} {ttft:>7.0f}ms {first_sent:>7.0f}ms {total:>7.0f}ms {tokens:>5} {itl:>5.0f}ms"
            )

        # --- Part 2: Multi-turn test ---
        print()
        print("-" * 80)
        print("PART 2: Multi-Turn Test (5 turns of context)")
        print("-" * 80)

        multi_turn_messages = MULTI_TURN_HISTORY + [
            {"role": "user", "content": "మధ్యాహ్నం 3 గంటలకు పెట్టుకోండి"}
        ]
        result = await benchmark_sarvam(client, SYSTEM_PROMPT_TELUGU, multi_turn_messages)

        if "error" in result:
            print(f"Multi-turn: ERROR: {result['error']}")
        else:
            print(
                f"5-turn context:  TTFT={result['ttft_ms']:.0f}ms  "
                f"1st_sentence={result['first_sentence_ms']:.0f}ms  "
                f"total={result['total_ms']:.0f}ms  "
                f"tokens={result['tokens']}  "
                f"ITL={result['avg_itl_ms']:.0f}ms"
            )
            print(f"Response: {result['response']}")
            sarvam_ttfts.append(result["ttft_ms"])

        # --- Part 3: OpenRouter comparison ---
        if OPENROUTER_API_KEY:
            print()
            print("-" * 80)
            print("PART 3: OpenRouter Comparison (Llama 3.3 70B)")
            print("-" * 80)
            print(f"{'Test':<35} {'TTFT':>8} {'Total':>8} {'Tokens':>6} {'ITL':>6}")
            print("-" * 80)

            or_ttfts = []
            for lang, sys_prompt, user_msg, desc in TEST_CASES[:3]:  # Just Telugu tests
                messages = [{"role": "user", "content": user_msg}]
                result = await benchmark_openrouter(client, sys_prompt, messages)

                if "error" in result:
                    print(f"{desc:<35} ERROR: {result['error'][:40]}")
                    continue

                ttft = result["ttft_ms"]
                or_ttfts.append(ttft)
                total = result["total_ms"]
                tokens = result["tokens"]
                itl = result["avg_itl_ms"]

                print(f"{desc:<35} {ttft:>7.0f}ms {total:>7.0f}ms {tokens:>5} {itl:>5.0f}ms")

            if or_ttfts:
                print(f"\nOpenRouter Avg TTFT: {sum(or_ttfts)/len(or_ttfts):.0f}ms")

        # --- Summary ---
        print()
        print("=" * 80)
        print("SUMMARY")
        print("=" * 80)

        if sarvam_ttfts:
            avg_ttft = sum(sarvam_ttfts) / len(sarvam_ttfts)
            min_ttft = min(sarvam_ttfts)
            max_ttft = max(sarvam_ttfts)

            print(f"\nSarvam {SARVAM_LLM_MODEL}:")
            print(f"  Avg TTFT:  {avg_ttft:.0f}ms")
            print(f"  Min TTFT:  {min_ttft:.0f}ms")
            print(f"  Max TTFT:  {max_ttft:.0f}ms")

            print(f"\nDECISION:")
            if avg_ttft < 500:
                print(f"  EXCELLENT ({avg_ttft:.0f}ms) — Minimal fillers needed")
                print(f"  -> Use Sarvam for all languages")
            elif avg_ttft < 800:
                print(f"  GOOD ({avg_ttft:.0f}ms) — Standard fillers (300ms)")
                print(f"  -> Use Sarvam for all languages")
            elif avg_ttft < 1500:
                print(f"  ACCEPTABLE ({avg_ttft:.0f}ms) — Extended fillers (500-800ms)")
                print(f"  -> Use Sarvam for all languages, longer fillers needed")
            else:
                print(f"  SLOW ({avg_ttft:.0f}ms) — Hybrid approach recommended")
                print(f"  -> Sarvam for Indian languages (with long fillers)")
                print(f"  -> Groq/OpenRouter for English-only calls")
        else:
            print("\nNo successful Sarvam tests. Check API key and model name.")
            print(f"   Model tried: {SARVAM_LLM_MODEL}")
            print(f"   URL: {SARVAM_LLM_BASE_URL}")
            print("   Try changing SARVAM_LLM_MODEL in .env")

        print()


if __name__ == "__main__":
    asyncio.run(run_benchmarks())
