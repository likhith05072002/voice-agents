"""Tests for eagerness presets."""

from src.pipeline.eagerness import resolve, PROFILES


_CUSTOM = dict(min_words=2, false_timeout_s=1.0, speech_end_grace_s=0.3,
               continuation_timeout_s=0.6, high_vad_sensitivity=True)


def test_known_presets_resolve():
    assert resolve("eager", **_CUSTOM) is PROFILES["eager"]
    assert resolve("CAUTIOUS", **_CUSTOM) is PROFILES["cautious"]


def test_unknown_falls_back_to_custom_values():
    p = resolve("whatever", **{**_CUSTOM, "min_words": 5})
    assert p.min_words == 5                      # used the custom knobs
    assert p not in PROFILES.values()


def test_preset_ordering_is_monotonic():
    cautious, balanced, eager = PROFILES["cautious"], PROFILES["balanced"], PROFILES["eager"]
    # eager interrupts/answers sooner -> smaller thresholds
    assert eager.false_timeout_s < balanced.false_timeout_s < cautious.false_timeout_s
    assert eager.continuation_timeout_s < balanced.continuation_timeout_s < cautious.continuation_timeout_s
    assert eager.min_words <= balanced.min_words <= cautious.min_words


def test_cautious_lowers_vad_sensitivity():
    assert PROFILES["cautious"].high_vad_sensitivity is False
    assert PROFILES["eager"].high_vad_sensitivity is True
