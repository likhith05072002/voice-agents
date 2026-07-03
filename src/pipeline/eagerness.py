"""Eagerness presets — one dial for the speed-vs-premature-cutoff trade-off.

Instead of hand-tuning five barge-in/endpointing knobs, pick a preset:

  - ``cautious``  — never cut the caller off; wait longer, interrupt less.
  - ``balanced``  — the default (matches the engine's standalone defaults).
  - ``eager``     — snappiest responses; interrupts and answers sooner.

Any other value is treated as ``custom`` and resolves to the individually
configured knobs, so power users keep full control.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EagernessProfile:
    min_words: int
    false_timeout_s: float
    speech_end_grace_s: float
    continuation_timeout_s: float
    high_vad_sensitivity: bool


PROFILES: dict[str, EagernessProfile] = {
    "cautious": EagernessProfile(
        min_words=3, false_timeout_s=1.6, speech_end_grace_s=0.5,
        continuation_timeout_s=0.9, high_vad_sensitivity=False),
    "balanced": EagernessProfile(
        min_words=2, false_timeout_s=1.2, speech_end_grace_s=0.3,
        continuation_timeout_s=0.6, high_vad_sensitivity=True),
    "eager": EagernessProfile(
        min_words=1, false_timeout_s=0.8, speech_end_grace_s=0.2,
        continuation_timeout_s=0.4, high_vad_sensitivity=True),
}


def resolve(name: str, *, min_words: int, false_timeout_s: float,
            speech_end_grace_s: float, continuation_timeout_s: float,
            high_vad_sensitivity: bool) -> EagernessProfile:
    """Return the named preset, or a ``custom`` profile from the given knobs."""
    preset = PROFILES.get((name or "").lower())
    if preset is not None:
        return preset
    return EagernessProfile(
        min_words=min_words, false_timeout_s=false_timeout_s,
        speech_end_grace_s=speech_end_grace_s,
        continuation_timeout_s=continuation_timeout_s,
        high_vad_sensitivity=high_vad_sensitivity)
