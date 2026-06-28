"""Filler audio system — plays natural sounds ("hmm", "acchaa") while LLM thinks.

Fillers play IMMEDIATELY on turn detection (~0ms delay).
This masks the LLM+TTS latency (~688ms) with natural audio.

Pre-generated fillers are stored as mu-law 8kHz PCM files.
In production, generate these using Sarvam TTS at startup.
For now, we generate synthetic silence + tone as placeholder.
"""

import asyncio
import os
import random
import struct
from pathlib import Path

import structlog

logger = structlog.get_logger()

FILLER_DIR = Path(__file__).parent.parent.parent / "assets" / "fillers"

# Filler categories by context
FILLERS = {
    "te-IN": {
        "acknowledge": ["avunu", "sare", "aunu"],
        "thinking": ["hmm", "chustanu", "oka_nimisham"],
        "greeting": ["namaskaram"],
    },
    "hi-IN": {
        "acknowledge": ["haan", "ji", "acchaa"],
        "thinking": ["hmm", "dekhti_hoon", "ek_minute"],
        "greeting": ["namaste"],
    },
    "kn-IN": {
        "acknowledge": ["haudu", "sari"],
        "thinking": ["hmm", "nodteeni"],
        "greeting": ["namaskara"],
    },
    "ta-IN": {
        "acknowledge": ["aama", "sari"],
        "thinking": ["hmm", "paarkiren"],
        "greeting": ["vanakkam"],
    },
    "en-IN": {
        "acknowledge": ["sure", "okay"],
        "thinking": ["hmm", "let_me_check"],
        "greeting": ["hello"],
    },
}


def _generate_silence_mulaw(duration_ms: int, sample_rate: int = 8000) -> bytes:
    """Generate mu-law encoded silence (for placeholder fillers)."""
    num_samples = int(sample_rate * duration_ms / 1000)
    # mu-law silence is 0xFF (positive zero)
    return bytes([0xFF] * num_samples)


def _generate_tone_mulaw(duration_ms: int, freq: float = 200, sample_rate: int = 8000) -> bytes:
    """Generate a soft 'hmm' tone in mu-law (placeholder)."""
    import math

    num_samples = int(sample_rate * duration_ms / 1000)
    samples = []
    for i in range(num_samples):
        t = i / sample_rate
        # Soft tone with fade in/out
        envelope = min(t * 10, 1.0) * min((duration_ms / 1000 - t) * 10, 1.0)
        val = int(1000 * envelope * math.sin(2 * math.pi * freq * t))
        # Clamp
        val = max(-32768, min(32767, val))
        samples.append(struct.pack("<h", val))

    pcm = b"".join(samples)
    import audioop
    return audioop.lin2ulaw(pcm, 2)


class FillerPlayer:
    """Selects and returns filler audio based on context."""

    def __init__(self, language: str = "te-IN"):
        self.language = language
        self._cache: dict[str, bytes] = {}
        self._last_used: str | None = None

    def load(self) -> None:
        """Load filler audio files from disk.
        Files are PCM16 8kHz from Sarvam TTS (generated with speech_sample_rate=8000).
        NO resampling needed — they're already at 8kHz."""
        lang_dir = FILLER_DIR / self.language.replace("-", "_")

        if lang_dir.exists():
            for f in lang_dir.glob("*.raw"):
                self._cache[f.stem] = f.read_bytes()  # Already PCM16 8kHz
            logger.info("filler.loaded_from_disk", language=self.language, count=len(self._cache))
        else:
            self._cache["hmm"] = b"\x00\x00" * 3200  # 400ms silence at 8kHz PCM16
            self._cache["silence"] = b"\x00\x00" * 2400  # 300ms
            logger.info("filler.using_placeholders", language=self.language)

    def select(self, transcript: str = "") -> bytes:
        """Select a contextually appropriate filler audio.

        Returns mu-law 8kHz audio bytes ready for telephony.
        """
        if not self._cache:
            self.load()

        # Simple selection logic
        category = "thinking"
        lower = transcript.lower()
        if any(w in lower for w in ["hello", "hi", "namask", "vanakk"]):
            category = "greeting"

        # Get available fillers for this category
        lang_fillers = FILLERS.get(self.language, FILLERS["en-IN"])
        options = lang_fillers.get(category, lang_fillers["thinking"])

        # Pick one we haven't used recently
        candidates = [f for f in options if f != self._last_used and f in self._cache]
        if not candidates:
            candidates = [f for f in self._cache.keys() if f != self._last_used]
        if not candidates:
            candidates = list(self._cache.keys())

        choice = random.choice(candidates) if candidates else "hmm"
        self._last_used = choice

        audio = self._cache.get(choice, self._cache.get("hmm", _generate_silence_mulaw(300)))
        return audio

    def get_silence(self, duration_ms: int = 200) -> bytes:
        """Get silence audio of specified duration."""
        return _generate_silence_mulaw(duration_ms)
