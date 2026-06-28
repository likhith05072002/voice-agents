"""Generate filler audio files using Sarvam TTS.

Creates short audio clips ("hmm", "acchaa", "sare") in all supported languages.
Output: mu-law 8kHz raw audio files in assets/fillers/{language}/

Run: python scripts/generate_fillers.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import websockets

API_KEY = os.getenv("SARVAM_API_KEY")
ASSETS_DIR = Path(__file__).parent.parent / "assets" / "fillers"

FILLERS_BY_LANG = {
    "te-IN": {
        "hmm": "హ్మ్మ్...",
        "avunu": "అవును...",
        "sare": "సరే...",
        "chustanu": "చూస్తాను...",
        "oka_nimisham": "ఒక నిమిషం...",
        "namaskaram": "నమస్కారం!",
        "aunu": "ఔను...",
    },
    "hi-IN": {
        "hmm": "हम्म्...",
        "haan": "हाँ...",
        "ji": "जी...",
        "acchaa": "अच्छा...",
        "dekhti_hoon": "देखती हूँ...",
        "ek_minute": "एक मिनट...",
        "namaste": "नमस्ते!",
    },
    "kn-IN": {
        "hmm": "ಹ್ಮ್ಮ್...",
        "haudu": "ಹೌದು...",
        "sari": "ಸರಿ...",
        "nodteeni": "ನೋಡ್ತೀನಿ...",
        "namaskara": "ನಮಸ್ಕಾರ!",
    },
    "ta-IN": {
        "hmm": "ம்ம்...",
        "aama": "ஆமா...",
        "sari": "சரி...",
        "paarkiren": "பார்க்கிறேன்...",
        "vanakkam": "வணக்கம்!",
    },
    "en-IN": {
        "hmm": "Hmm...",
        "sure": "Sure...",
        "okay": "Okay...",
        "let_me_check": "Let me check...",
        "hello": "Hello!",
    },
}


async def generate_filler(text: str, language: str, voice: str = "anushka") -> bytes | None:
    """Generate a single filler audio using Sarvam TTS WebSocket."""
    try:
        ws = await websockets.connect(
            "wss://api.sarvam.ai/text-to-speech/ws",
            additional_headers={"api-subscription-key": API_KEY},
        )

        config = {
            "type": "config",
            "data": {
                "target_language_code": language,
                "speaker": voice,
                "model": "bulbul:v2",
                "speech_sample_rate": "8000",
                "send_completion_event": True,
            },
        }
        await ws.send(json.dumps(config))
        await ws.send(json.dumps({"type": "text", "data": {"text": text}}))
        await ws.send(json.dumps({"type": "flush"}))

        import base64
        audio_chunks = []
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
                msg = json.loads(raw)
                if msg.get("type") == "audio":
                    audio_chunks.append(base64.b64decode(msg["data"]["audio"]))
                elif msg.get("type") in ("event", "error"):
                    break
            except asyncio.TimeoutError:
                break  # No more audio coming

        await ws.close()
        return b"".join(audio_chunks) if audio_chunks else None

    except Exception as e:
        print(f"  Error generating '{text}': {type(e).__name__}: {e}")
        return None


async def main():
    print("Generating filler audio files using Sarvam TTS...")
    print(f"Output: {ASSETS_DIR}")
    print()

    total_generated = 0
    total_cost_chars = 0

    for lang, fillers in FILLERS_BY_LANG.items():
        lang_dir = ASSETS_DIR / lang.replace("-", "_")
        lang_dir.mkdir(parents=True, exist_ok=True)

        print(f"Language: {lang}")
        for name, text in fillers.items():
            filepath = lang_dir / f"{name}.raw"
            if filepath.exists():
                print(f"  {name}: already exists, skipping")
                continue

            audio = await generate_filler(text, lang)
            if audio:
                filepath.write_bytes(audio)
                duration_ms = len(audio) / 8  # mu-law 8kHz = 1 byte per sample
                print(f"  {name}: {len(audio)} bytes ({duration_ms:.0f}ms) -> {filepath.name}")
                total_generated += 1
                total_cost_chars += len(text)
            else:
                print(f"  {name}: FAILED")

            await asyncio.sleep(0.3)  # Don't hammer API

        print()

    print(f"Done! Generated {total_generated} fillers.")
    print(f"Total chars used: {total_cost_chars} (~{total_cost_chars/10000*15:.2f} INR)")


if __name__ == "__main__":
    asyncio.run(main())
