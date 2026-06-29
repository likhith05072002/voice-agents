"""Audio codec utilities for telephony format conversion.

Telephony (EnableX/Telnyx): mu-law, 8kHz, mono, base64
Sarvam STT: PCM_S16LE, 16kHz, mono
Sarvam TTS: mu-law, 8kHz, mono (configured via output_audio_codec="mulaw")
"""

import audioop
import base64
import struct

import numpy as np


def ulaw_to_pcm16(ulaw_bytes: bytes) -> bytes:
    """Convert mu-law audio to 16-bit PCM."""
    return audioop.ulaw2lin(ulaw_bytes, 2)


def alaw_to_pcm16(alaw_bytes: bytes) -> bytes:
    """Convert A-law audio to 16-bit PCM."""
    return audioop.alaw2lin(alaw_bytes, 2)


def pcm16_to_ulaw(pcm_bytes: bytes) -> bytes:
    """Convert 16-bit PCM to mu-law."""
    return audioop.lin2ulaw(pcm_bytes, 2)


def pcm16_to_alaw(pcm_bytes: bytes) -> bytes:
    """Convert 16-bit PCM to A-law."""
    return audioop.lin2alaw(pcm_bytes, 2)


def resample_8k_to_16k(pcm_8k: bytes) -> bytes:
    """Stateless 8kHz->16kHz PCM16 resample.

    Prefer :class:`Resampler` for streaming audio — a stateless call resets the
    filter on every chunk and introduces an audible boundary click. This helper
    exists for one-shot conversions and tests only.
    """
    result, _ = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, None)
    return result


def resample_16k_to_8k(pcm_16k: bytes) -> bytes:
    """Stateless 16kHz->8kHz PCM16 resample. See :class:`Resampler` for streams."""
    result, _ = audioop.ratecv(pcm_16k, 2, 1, 16000, 8000, None)
    return result


class Resampler:
    """Per-call stateful resampler.

    ``audioop.ratecv`` carries filter state between chunks; sharing that state
    across calls (a module global) corrupts concurrent calls and clicks at chunk
    boundaries. Each call owns one instance so its up/down state is isolated.
    """

    def __init__(self) -> None:
        self._up_state = None
        self._down_state = None

    def up_8k_to_16k(self, pcm_8k: bytes) -> bytes:
        out, self._up_state = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, self._up_state)
        return out

    def down_16k_to_8k(self, pcm_16k: bytes) -> bytes:
        out, self._down_state = audioop.ratecv(pcm_16k, 2, 1, 16000, 8000, self._down_state)
        return out


def b64_decode(data: str) -> bytes:
    return base64.b64decode(data)


def b64_encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def telephony_to_stt(ulaw_b64: str) -> bytes:
    """Convert telephony audio (mu-law b64 8kHz) to STT format (PCM16 16kHz)."""
    ulaw = b64_decode(ulaw_b64)
    pcm_8k = ulaw_to_pcm16(ulaw)
    pcm_16k = resample_8k_to_16k(pcm_8k)
    return pcm_16k


def tts_to_telephony(mulaw_audio: bytes) -> str:
    """Convert TTS output (mu-law 8kHz raw) to telephony format (b64).
    Since TTS is configured with output_audio_codec=mulaw and sample_rate=8000,
    the audio is already in the right format — just base64 encode."""
    return b64_encode(mulaw_audio)
