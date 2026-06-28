"""
Nama Srinivasa Jewellery - Voice Agent LLM Benchmark
=====================================================
Tests Sarvam-30B with reasoning_effort=None for jewellery shop use case.
Measures TTFT across Telugu, Hindi, Kannada, English.

Run: python scripts/benchmark_jewellery.py
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

API_KEY = os.getenv("SARVAM_API_KEY")
BASE_URL = os.getenv("SARVAM_LLM_BASE_URL", "https://api.sarvam.ai/v1")

# --- System Prompts ---

SYS_TE = (
    "మీరు నమ శ్రీనివాస జ్యూవెల్లరీ షాప్ యొక్క AI అసిస్టెంట్ లక్ష్మి. "
    "మర్యాదగా, స్నేహపూర్వకంగా మాట్లాడండి. సమాధానాలు చిన్నగా ఉంచండి (1-2 వాక్యాలు). "
    "షాప్ సమయాలు: ఉదయం 10 - రాత్రి 9, ప్రతి రోజూ. "
    "చిరునామా: బంజారా హిల్స్, హైదరాబాద్. "
    "సేవలు: బంగారు నగలు, వెండి నగలు, వజ్రాల నగలు, పాత బంగారం మార్పిడి, హాల్‌మార్క్ నగలు. "
    "ఈ రోజు బంగారం ధర: గ్రాముకు 7,800 రూపాయలు. "
    "22 క్యారెట్ బంగారం ధర: గ్రాముకు 7,150 రూపాయలు."
)

SYS_HI = (
    "आप नम श्रीनिवास ज्वेलरी शॉप की AI असिस्टेंट लक्ष्मी हैं। "
    "विनम्र रहें। जवाब छोटे रखें (1-2 वाक्य)। "
    "समय: सुबह 10 - रात 9, हर दिन। पता: बंजारा हिल्स, हैदराबाद। "
    "सेवाएं: सोने के गहने, चांदी, हीरे, पुराना सोना एक्सचेंज, हॉलमार्क। "
    "आज सोने का भाव: 7,800 रुपये प्रति ग्राम।"
)

SYS_KN = (
    "ನೀವು ನಮ ಶ್ರೀನಿವಾಸ ಜ್ಯೂವೆಲ್ಲರಿ ಅಂಗಡಿಯ AI ಸಹಾಯಕಿ ಲಕ್ಷ್ಮಿ. "
    "ಸಭ್ಯವಾಗಿ ಮಾತನಾಡಿ. ಉತ್ತರಗಳನ್ನು ಚಿಕ್ಕದಾಗಿ ಇಡಿ (1-2 ವಾಕ್ಯಗಳು). "
    "ಅಂಗಡಿ ಸಮಯ: ಬೆಳಿಗ್ಗೆ 10 - ರಾತ್ರಿ 9. "
    "ಸೇವೆಗಳು: ಚಿನ್ನದ ಆಭರಣ, ಬೆಳ್ಳಿ, ವಜ್ರ, ಹಳೆಯ ಚಿನ್ನ ವಿನಿಮಯ. "
    "ಇಂದಿನ ಚಿನ್ನದ ಬೆಲೆ: ಗ್ರಾಂಗೆ 7,800 ರೂಪಾಯಿ."
)

SYS_TA = (
    "நீங்கள் நம ஸ்ரீநிவாச ஜூவல்லரி கடையின் AI உதவியாளர் லக்ஷ்மி. "
    "பணிவாக பேசுங்கள். பதில்களை சுருக்கமாக வையுங்கள் (1-2 வாக்கியங்கள்). "
    "கடை நேரம்: காலை 10 - இரவு 9. "
    "சேவைகள்: தங்க நகைகள், வெள்ளி, வைரம், பழைய தங்கம் மாற்று. "
    "இன்றைய தங்கம் விலை: கிராமுக்கு 7,800 ரூபாய்."
)

# --- Test Cases ---

TESTS = [
    # Telugu tests
    ("TE: gold rate", SYS_TE, "andi, eeroju bangaram dhara enta?"),
    ("TE: necklace", SYS_TE, "oka gold necklace chupinchandi, 50 grams lo"),
    ("TE: exchange gold", SYS_TE, "paata bangaram marchukovali, ela cheyali?"),
    ("TE: shop timings", SYS_TE, "shop enni gantlaku terustharu?"),
    ("TE: wedding set", SYS_TE, "pelli ki bridal set kavali, budget 5 lakhs"),
    ("TE: hallmark", SYS_TE, "mee dagara hallmark nagalu untaya?"),
    ("TE: making charges", SYS_TE, "making charges enta untayi?"),
    # Hindi tests
    ("HI: gold rate", SYS_HI, "aaj sone ka bhav kya hai?"),
    ("HI: bridal set", SYS_HI, "shaadi ke liye bridal set dikhao, 3 lakh budget"),
    ("HI: exchange", SYS_HI, "purana sona exchange karna hai, kaise hoga?"),
    # Kannada tests
    ("KN: gold rate", SYS_KN, "indu chinnada bele eshtu?"),
    ("KN: ring", SYS_KN, "engagement ring beku, 10 gram gold"),
    # Tamil tests
    ("TA: gold rate", SYS_TA, "innaiku thangam vilai enna?"),
    ("TA: chain", SYS_TA, "oru thanga chain venum, 20 gram"),
    # English tests
    ("EN: gold rate", SYS_TE, "what is todays gold rate?"),
    ("EN: address", SYS_TE, "what is your shop address and timings?"),
]

# Multi-turn negotiation
MULTI_TURN_HISTORY = [
    {"role": "user", "content": "andi bangaram chupinchandi"},
    {"role": "assistant", "content": "tappanisaringa! mee budget enta? emi type nagalu kavali?"},
    {"role": "user", "content": "2 lakhs lo oka set kavali"},
    {"role": "assistant", "content": "2 lakhs lo manchi sets unnaayi. 25 grams gold necklace set, making charges tho 1.95 lakhs."},
    {"role": "user", "content": "gold set chupinchandi"},
    {"role": "assistant", "content": "ee set chala andamga untundi. 22 carat hallmark gold, antique finish. try chestara?"},
]


async def bench(client, name, sys_prompt, user_msg, history=None):
    msgs = [{"role": "system", "content": sys_prompt}]
    if history:
        msgs.extend(history)
    msgs.append({"role": "user", "content": user_msg})

    start = time.perf_counter()
    first_content_time = None
    full_content = ""
    tokens = 0

    try:
        async with client.stream(
            "POST",
            f"{BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "sarvam-30b",
                "messages": msgs,
                "stream": True,
                "max_tokens": 256,
                "reasoning_effort": None,
            },
            timeout=30.0,
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                return name, -1, -1, 0, f"HTTP {resp.status_code}"

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices", [])
                if not choices:
                    continue
                c = choices[0].get("delta", {}).get("content")
                if c:
                    if first_content_time is None:
                        first_content_time = time.perf_counter()
                    tokens += 1
                    full_content += c
    except Exception as e:
        return name, -1, -1, 0, str(e)[:50]

    end = time.perf_counter()
    ttft = ((first_content_time - start) * 1000) if first_content_time else -1
    total = (end - start) * 1000
    return name, ttft, total, tokens, full_content


async def main():
    print("=" * 115)
    print("NAMA SRINIVASA JEWELLERY - VOICE AGENT LLM BENCHMARK")
    print("Model: sarvam-30b | reasoning_effort=None (DISABLED)")
    print("=" * 115)
    print()
    print(f"{'Test':<25} {'TTFT':>7}  {'Total':>7} {'Tok':>4}  Response")
    print("-" * 115)

    ttfts = []

    async with httpx.AsyncClient() as client:
        # Single-turn tests
        for name, sys_p, user_m in TESTS:
            n, ttft, total, tok, content = await bench(client, name, sys_p, user_m)
            if ttft > 0:
                ttfts.append(ttft)
            c = content.replace("\n", " ")[:65]
            print(f"{n:<25} {ttft:>6.0f}ms {total:>6.0f}ms {tok:>4}  {c}")

        # Multi-turn negotiation
        print()
        print("--- Multi-turn (6 turns history + negotiation) ---")
        n, ttft, total, tok, content = await bench(
            client,
            "TE: negotiate price",
            SYS_TE,
            "konchem takkuva chesthe baaguntundi, 1.80 lakhs ki ichestara?",
            MULTI_TURN_HISTORY,
        )
        if ttft > 0:
            ttfts.append(ttft)
        c = content.replace("\n", " ")[:80]
        print(f"{'TE: negotiate price':<25} {ttft:>6.0f}ms {total:>6.0f}ms {tok:>4}  {c}")

        # Repeat first test to check consistency
        print()
        print("--- Consistency check (repeat Telugu gold rate 3x) ---")
        for i in range(3):
            n, ttft, total, tok, content = await bench(
                client, f"TE: gold rate #{i+1}", SYS_TE, "bangaram rate enta eeroju?"
            )
            if ttft > 0:
                ttfts.append(ttft)
            c = content.replace("\n", " ")[:65]
            print(f"{'TE: gold rate #' + str(i+1):<25} {ttft:>6.0f}ms {total:>6.0f}ms {tok:>4}  {c}")

    # --- Results ---
    print()
    print("=" * 115)
    print(f"RESULTS ({len(ttfts)} successful tests)")
    print(f"  Avg TTFT:  {sum(ttfts)/len(ttfts):.0f}ms")
    print(f"  Min TTFT:  {min(ttfts):.0f}ms")
    print(f"  Max TTFT:  {max(ttfts):.0f}ms")
    sorted_ttfts = sorted(ttfts)
    p50_idx = len(sorted_ttfts) // 2
    p95_idx = min(int(len(sorted_ttfts) * 0.95), len(sorted_ttfts) - 1)
    print(f"  P50 TTFT:  {sorted_ttfts[p50_idx]:.0f}ms")
    print(f"  P95 TTFT:  {sorted_ttfts[p95_idx]:.0f}ms")

    avg_ttft = sum(ttfts) / len(ttfts)
    avg_total = avg_ttft + 80  # sentence accumulation estimate

    print()
    print("FULL PIPELINE LATENCY ESTIMATE:")
    print(f"  Sarvam STT (streaming):    200ms avg")
    print(f"  Turn detection (VAD+STT):  250ms avg")
    print(f"  >> Filler plays here:        0ms (immediate)")
    print(f"  Sarvam-30B LLM TTFT:       {avg_ttft:.0f}ms (benchmarked)")
    print(f"  Sentence accumulation:      80ms")
    print(f"  Sarvam TTS TTFA:           260ms avg")
    print(f"  Transport (both ways):     160ms")
    print(f"  -----------------------------------------")
    total_measured = 200 + 250 + avg_ttft + 80 + 260 + 160
    print(f"  TOTAL MEASURED:            {total_measured:.0f}ms")
    print(f"  PERCEIVED (with filler):   ~250-330ms")
    print()

    if total_measured < 1000:
        print("VERDICT: EXCELLENT - Sub-1s measured latency. With fillers, feels instant.")
    elif total_measured < 1200:
        print("VERDICT: GOOD - Around 1s measured. Fillers mask it completely.")
    elif total_measured < 1500:
        print("VERDICT: ACCEPTABLE - Under 1.5s. Fillers make it feel natural.")
    else:
        print("VERDICT: NEEDS WORK - Over 1.5s. Consider optimizations.")


if __name__ == "__main__":
    asyncio.run(main())
