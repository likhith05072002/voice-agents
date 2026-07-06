"""Phase 0 spike for the emotion/non-verbal voice feature.

Answers two make-or-break questions BY EAR, before we build any pipeline:
  1. Does Bulbul/neha produce usable non-verbals (laugh, hum, backchannel) from
     PLAIN TEXT? If yes, most of the taxonomy can be text-only — zero splicing,
     perfect voice identity.
  2. If we DO splice a pre-generated neha clip into a sentence, is the seam
     audible? (constant-power crossfade + zero-crossing snap)

Renders everything in the neha voice at 16 kHz (web quality) and writes WAVs +
a manifest that the /lab/nonverbal page serves so you can listen + A/B.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import wave

import httpx
import numpy as np

from src.config import settings

RATE = 16000
OUT = os.path.join("data", "nonverbal_lab")
KEY = settings.sarvam_api_key or os.environ.get("SARVAM_API_KEY", "")


async def render(text: str, lang: str = "en-IN") -> bytes:
    """One neha render at 16 kHz -> PCM16 mono bytes."""
    async with httpx.AsyncClient(timeout=30.0) as c:
        body = {"text": text, "target_language_code": lang, "speaker": "neha",
                "model": "bulbul:v3", "speech_sample_rate": RATE}
        r = await c.post("https://api.sarvam.ai/text-to-speech",
                         headers={"api-subscription-key": KEY, "content-type": "application/json"},
                         json=body)
        r.raise_for_status()
        raw = base64.b64decode(r.json()["audios"][0])
    with wave.open(io.BytesIO(raw)) as w:
        pcm = w.readframes(w.getnframes())
        if w.getframerate() != RATE:  # shouldn't happen, but be safe
            import audioop
            pcm, _ = audioop.ratecv(pcm, 2, 1, w.getframerate(), RATE, None)
    return pcm


def trim(pcm: bytes, thresh: int = 120, frame: int = 160) -> bytes:
    """Gently strip leading/trailing near-silence (low threshold so soft
    breaths/hums survive)."""
    a = np.frombuffer(pcm, np.int16)
    n = len(a) // frame
    if n == 0:
        return pcm
    rms = np.array([np.sqrt(np.mean(a[i * frame:(i + 1) * frame].astype(np.float32) ** 2))
                    for i in range(n)])
    loud = np.where(rms > thresh)[0]
    if len(loud) == 0:
        return pcm
    s, e = loud[0] * frame, (loud[-1] + 1) * frame
    return a[s:e].tobytes()


def _snap_zero(a: np.ndarray, frm_end: bool) -> int:
    """Return an index near the boundary where the waveform crosses zero, to
    avoid a click at the splice. Searches a small window."""
    win = min(80, len(a) - 1)
    if frm_end:  # snap the TAIL: search backwards from the end
        seg = a[-win:]
        for i in range(len(seg) - 1, 0, -1):
            if (seg[i - 1] <= 0 <= seg[i]) or (seg[i - 1] >= 0 >= seg[i]):
                return len(a) - win + i
        return len(a)
    else:  # snap the HEAD: search forward from the start
        seg = a[:win]
        for i in range(1, len(seg)):
            if (seg[i - 1] <= 0 <= seg[i]) or (seg[i - 1] >= 0 >= seg[i]):
                return i
        return 0


def splice(a: bytes, b: bytes, xfade_ms: int = 12, gap_ms: int = 0) -> bytes:
    """Constant-power crossfade of clip `a`'s tail into `b`'s head (a then b).
    Snaps both join points to zero-crossings. Optional silent gap between."""
    A = np.frombuffer(a, np.int16).astype(np.float32)
    B = np.frombuffer(b, np.int16).astype(np.float32)
    A = A[:_snap_zero(A, True)]
    B = B[_snap_zero(B, False):]
    n = int(RATE * xfade_ms / 1000)
    n = min(n, len(A), len(B))
    if gap_ms > 0:
        gap = np.zeros(int(RATE * gap_ms / 1000), np.float32)
        A = np.concatenate([A, gap])
        n = min(int(RATE * xfade_ms / 1000), len(gap), len(B))
    if n <= 4:
        out = np.concatenate([A, B])
    else:
        t = np.linspace(0, 1, n)
        fout, fin = np.sqrt(1 - t), np.sqrt(t)          # constant-power (not linear)
        cross = A[-n:] * fout + B[:n] * fin
        out = np.concatenate([A[:-n], cross, B[n:]])
    return np.clip(out, -32768, 32767).astype(np.int16).tobytes()


def save_wav(name: str, pcm: bytes) -> None:
    with wave.open(os.path.join(OUT, f"{name}.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(pcm)


async def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    manifest: list[dict] = []

    def add(name, label, pcm, group):
        save_wav(name, trim(pcm))
        manifest.append({"name": name, "label": label, "group": group})

    # GROUP 1 — can neha LAUGH from plain text?
    laughs = {
        "laugh_haha": "Haha!",
        "laugh_hahaha": "Ha ha ha.",
        "laugh_hehe": "Hehe.",
        "laugh_ctx": "Haha, that's a good one!",
        "laugh_soft": "Heh, no problem at all.",
    }
    # GROUP 2 — hums / thinking / acknowledgment (English)
    hums = {
        "hum_hmm": "Hmm.",
        "hum_thinking": "Hmm, let me see.",
        "hum_mmm": "Mmm.",
        "hum_uhhuh": "Uh-huh.",
        "hum_oh": "Oh, I see.",
    }
    # GROUP 3 — Hindi backchannels (the real gold)
    hindi = {
        "hi_haan": ("हाँ।", "hi-IN"),
        "hi_acchaa": ("अच्छा।", "hi-IN"),
        "hi_ji": ("जी।", "hi-IN"),
        "hi_theekhai": ("ठीक है।", "hi-IN"),
        "hi_hmm": ("हम्म।", "hi-IN"),
    }
    # GROUP 4 — pauses & empathy purely via TEXT (ellipsis / em-dash)
    text_prosody = {
        "tx_pause": "Let me check that for you… one moment… okay, found it.",
        "tx_empathy": "Oh… I'm really sorry to hear that.",
        "tx_warm": "Sure — give me just a second.",
    }

    print("rendering group 1 (laughs)…")
    for n, t in laughs.items():
        add(n, t, await render(t), "1 · Laugh from text?")
    print("rendering group 2 (hums)…")
    for n, t in hums.items():
        add(n, t, await render(t), "2 · Hums / acknowledgment")
    print("rendering group 3 (hindi backchannels)…")
    for n, (t, lang) in hindi.items():
        add(n, t, await render(t, lang), "3 · Hindi backchannels")
    print("rendering group 4 (text prosody)…")
    for n, t in text_prosody.items():
        add(n, t, await render(t), "4 · Pauses & empathy via text")

    # GROUP 5 — THE A/B: text-only vs spliced clip, same line
    print("rendering group 5 (A/B splice test)…")
    base = trim(await render("Sure, let me check that for you."))
    hmm = trim(await render("Hmm."))
    haan = trim(await render("हाँ।", "hi-IN"))
    text_only = trim(await render("Hmm, sure, let me check that for you."))

    save_wav("ab_baseline", base)
    manifest.append({"name": "ab_baseline", "label": "baseline: 'Sure, let me check that for you.'", "group": "5 · A/B — text-only vs SPLICE"})
    save_wav("ab_textonly", text_only)
    manifest.append({"name": "ab_textonly", "label": "TEXT-ONLY: neha says 'Hmm, sure, let me check…' (no splice)", "group": "5 · A/B — text-only vs SPLICE"})
    save_wav("ab_spliced", splice(hmm, base, xfade_ms=12, gap_ms=60))
    manifest.append({"name": "ab_spliced", "label": "SPLICED: [Hmm clip] + crossfade + baseline", "group": "5 · A/B — text-only vs SPLICE"})
    save_wav("ab_spliced_haan", splice(haan, base, xfade_ms=12, gap_ms=60))
    manifest.append({"name": "ab_spliced_haan", "label": "SPLICED: [Haan clip] + baseline (Hindi backchannel splice)", "group": "5 · A/B — text-only vs SPLICE"})

    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"\nDONE — {len(manifest)} clips in {OUT}/  (listen at /lab/nonverbal)")


if __name__ == "__main__":
    asyncio.run(main())
