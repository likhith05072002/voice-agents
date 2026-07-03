"""Caller-voice rendering for the AI voice tester.

Renders scenario utterances to PCM16-8k with Sarvam's REST TTS (the WS endpoint
throttles bursts of connections; REST stays available and the tester's fixed
lines don't need streaming). Disk-cached by (text, language, voice), so repeat
test runs make zero TTS calls.
"""

from __future__ import annotations

import audioop
import base64
import hashlib
import os
import wave

import structlog

logger = structlog.get_logger()

CACHE_DIR = os.path.join("data", "tts_cache")


async def render_utterances(texts: list[str], language: str, voice: str,
                            api_key: str) -> dict[str, bytes]:
    """Render texts to PCM16-8k, disk-cached."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    out: dict[str, bytes] = {}
    misses = []
    for t in texts:
        key = hashlib.sha1(f"{t}|{language}|{voice}".encode()).hexdigest()
        path = os.path.join(CACHE_DIR, f"{key}.pcm")
        if os.path.exists(path):
            with open(path, "rb") as f:
                out[t] = _trim_silence(f.read())   # older cache entries are untrimmed
        else:
            misses.append((t, path))
    for text, path in misses:
        pcm = await _render_rest(text, language, voice, api_key)
        with open(path, "wb") as f:
            f.write(pcm)
        out[text] = pcm
    return out


async def _render_rest(text: str, language: str, voice: str, api_key: str) -> bytes:
    import io

    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Match the live WS model (v3) so pre-rendered audio (greetings) is the
        # SAME voice as mid-call TTS. Never silently swap the speaker: a wrong-
        # voice render would be cached under the requested voice's key and the
        # greeting would permanently sound like a different person.
        body = {"text": text, "target_language_code": language, "speaker": voice,
                "model": "bulbul:v3", "speech_sample_rate": 8000}
        resp = await client.post(
            "https://api.sarvam.ai/text-to-speech",
            headers={"api-subscription-key": api_key, "content-type": "application/json"},
            json=body)
        if resp.status_code == 400:            # voice/model mismatch: try v2, same voice
            body["model"] = "bulbul:v2"
            resp = await client.post(
                "https://api.sarvam.ai/text-to-speech",
                headers={"api-subscription-key": api_key,
                         "content-type": "application/json"},
                json=body)
        resp.raise_for_status()
        b64 = resp.json()["audios"][0]

    raw = base64.b64decode(b64)
    with wave.open(io.BytesIO(raw)) as w:
        pcm = w.readframes(w.getnframes())
        rate = w.getframerate()
    if rate != 8000:                            # normalize to telephony rate
        pcm, _ = audioop.ratecv(pcm, 2, 1, rate, 8000, None)
    if not pcm:
        raise RuntimeError(f"REST TTS returned no audio for: {text[:40]}")
    return _trim_silence(pcm)


def _trim_silence(pcm: bytes, threshold: int = 200, frame: int = 320) -> bytes:
    """Strip leading/trailing silence from rendered speech. Leading silence
    delays barge-in VAD triggering (measured ~1s of inflated stop-time) and
    skews latency anchors; trailing silence pads the 'caller stopped' moment."""
    n = len(pcm) // frame
    start, end = 0, n
    for i in range(n):
        if audioop.rms(pcm[i * frame:(i + 1) * frame], 2) > threshold:
            start = i
            break
    for i in range(n - 1, -1, -1):
        if audioop.rms(pcm[i * frame:(i + 1) * frame], 2) > threshold:
            end = i + 1
            break
    out = pcm[start * frame:end * frame]
    return out if out else pcm
