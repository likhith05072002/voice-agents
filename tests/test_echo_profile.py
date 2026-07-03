"""Tests for the per-call line-echo profiler."""

from src.audio.dsp import EchoProfile


def _learn(profile, delay, gain, frames=200, out_level=4000.0):
    """Simulate a greeting: agent speaks bursts, caller silent, echo comes back
    at `delay` frames with `gain`."""
    outs = []
    for t in range(frames):
        out = out_level if (t // 20) % 2 == 0 else 0.0     # talk/pause bursts
        outs.append(out)
        echo = gain * outs[t - delay] if t >= delay else 0.0
        profile.observe(out, echo + 30.0)                   # + line noise
    return outs


def test_learns_delay_and_gain():
    p = EchoProfile()
    _learn(p, delay=15, gain=0.2)
    assert p.delay == 15
    assert 0.15 < p.gain < 0.3


def test_echo_suppressed_but_caller_detected():
    p = EchoProfile()
    outs = _learn(p, delay=15, gain=0.2)
    # Agent still talking: outbound 4000 -> expected echo ~800.
    for _ in range(20):
        p.observe(4000.0, 800.0)
    thr = p.speech_threshold()
    assert thr > 800 * 1.5           # echo alone stays below threshold
    assert 2500 > thr                # a normal caller (~2500 RMS) crosses it


def test_agent_silent_threshold_drops_to_floor():
    p = EchoProfile()
    _learn(p, delay=10, gain=0.25)
    for _ in range(60):
        p.observe(0.0, 20.0)         # agent quiet -> no echo expected
    assert p.speech_threshold() == 400.0


def test_unlearned_uses_conservative_fallback():
    p = EchoProfile()
    p.observe(5000.0, 100.0)         # 1 frame only — not fitted yet
    assert p.delay is None
    assert p.speech_threshold() >= 400.0


def test_bump_gain_learns_from_false_recovery():
    p = EchoProfile()
    _learn(p, delay=15, gain=0.1)
    g0 = p.gain
    p.bump_gain()                      # engine says: that "speech" was echo
    assert p.gain > g0
    p.gain = 1.0
    p.bump_gain()
    assert p.gain == 1.0               # capped


def test_rolling_refit_tracks_louder_tts():
    """Gain learned on a quiet greeting must rise when live TTS is louder."""
    p = EchoProfile()
    _learn(p, delay=10, gain=0.1, out_level=2000.0)     # quiet greeting
    quiet_gain = p.gain
    # live answers: 2x louder source, real echo gain 0.3
    outs = []
    for t in range(300):
        out = 6000.0 if (t // 20) % 2 == 0 else 0.0
        outs.append(out)
        echo = 0.3 * outs[t - 10] if t >= 10 else 0.0
        p.feed_out(out)
        p.observe_in(echo + 30.0)
    assert p.gain > quiet_gain          # refit caught the true, higher gain


def test_gain_capped_at_unity():
    p = EchoProfile()
    # pathological fit data (inbound louder than outbound)
    for t in range(p.LEARN_FRAMES):
        p.observe(2000.0, 5000.0)
    assert p.gain <= 1.0
