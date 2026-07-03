"""Tests for jitter buffer + packet-loss concealment."""

import numpy as np

from src.audio.jitter import JitterBuffer, PacketLossConcealer
from src.audio.dsp import _to_pcm16, _to_float, _rms


def _frame(amp, seed=0):
    rng = np.random.default_rng(seed)
    return _to_pcm16(rng.standard_normal(160) * amp)


# ─── PLC ───

def test_plc_repeats_then_fades_to_silence():
    plc = PacketLossConcealer(fade=0.7, max_repeats=3)
    good = _frame(4000)
    plc.observe(good)
    c1 = plc.conceal()
    c2 = plc.conceal()
    assert _rms(_to_float(c1)) > _rms(_to_float(c2)) > 0   # fading
    plc.conceal(); plc.conceal()                            # exceed max_repeats
    assert _rms(_to_float(plc.conceal())) == 0.0            # silence


def test_plc_without_history_returns_empty():
    assert PacketLossConcealer().conceal() == b""


def test_plc_observe_resets_loss_count():
    plc = PacketLossConcealer(fade=0.5, max_repeats=3)
    g = _frame(4000)
    plc.observe(g)
    plc.conceal(); plc.conceal()
    plc.observe(g)                       # fresh frame resets the fade
    first = _to_float(plc.conceal())
    assert _rms(first) > 0.4 * _rms(_to_float(g))


# ─── JitterBuffer ───

def test_warms_up_before_releasing():
    jb = JitterBuffer(target=2, capacity=4)
    jb.push(_frame(3000, 1))
    assert jb.pull() == b""              # still filling (1 < target)
    jb.push(_frame(3000, 2))
    assert jb.pull() != b""              # warmed at 2


def test_overflow_drops_oldest():
    jb = JitterBuffer(target=1, capacity=3)
    for i in range(6):
        jb.push(_frame(3000, i))
    assert len(jb) == 3                  # capped at capacity


def test_underrun_yields_concealment():
    jb = JitterBuffer(target=1, capacity=4)
    f = _frame(5000, 7)
    jb.push(f)
    assert jb.pull() == f                       # real frame; now warmed + empty
    concealed = jb.pull()                       # underrun -> PLC frame
    assert concealed != b""
    assert _rms(_to_float(concealed)) < _rms(_to_float(f))   # a faded copy


def test_invalid_config_rejected():
    import pytest
    with pytest.raises(ValueError):
        JitterBuffer(target=0, capacity=4)
    with pytest.raises(ValueError):
        JitterBuffer(target=5, capacity=4)
