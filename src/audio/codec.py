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
    """Resample 8kHz PCM16 to 16kHz PCM16. Simple sample doubling (fast, good enough for speech)."""
    import struct
    samples = struct.unpack(f"<{len(pcm_8k)//2}h", pcm_8k)
    out = []
    for s in samples:
        out.append(s)
        out.append(s)  # Duplicate each sample for 2x upsample
    return struct.pack(f"<{len(out)}h", *out)


_ratecv_state_down = None


def resample_16k_to_8k(pcm_16k: bytes) -> bytes:
    """Resample 16kHz PCM16 to 8kHz PCM16."""
    global _ratecv_state_down
    result, _ratecv_state_down = audioop.ratecv(pcm_16k, 2, 1, 16000, 8000, _ratecv_state_down)
    return result


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
