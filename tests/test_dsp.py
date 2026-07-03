"""Tests for inbound audio DSP: noise gate, AGC, echo cancellation."""

import numpy as np

from src.audio.dsp import (
    NoiseGate, AGC, EchoCanceller, InboundDSP, SpectralDenoiser,
    erle_db, _to_pcm16, _to_float, _rms,
)


def _sine(freq, n, amp, sr=8000):
    t = np.arange(n) / sr
    return _to_pcm16(amp * np.sin(2 * np.pi * freq * t))


def _noise(n, amp, seed=0):
    rng = np.random.default_rng(seed)
    return _to_pcm16(rng.standard_normal(n) * amp)


# ─── NoiseGate ───

def test_noise_gate_attenuates_quiet_noise():
    gate = NoiseGate()
    quiet = _noise(160, amp=60)
    out = gate.process(quiet)
    assert _rms(_to_float(out)) < _rms(_to_float(quiet))


def test_noise_gate_passes_loud_speech():
    gate = NoiseGate()
    loud = _sine(300, 160, amp=6000)
    out = gate.process(loud)
    # loud frame passes essentially untouched
    assert _rms(_to_float(out)) > 0.9 * _rms(_to_float(loud))


# ─── AGC ───

def test_agc_amplifies_quiet_caller():
    agc = AGC(target_rms=3000.0, adapt=1.0)   # adapt=1 -> immediate
    quiet = _sine(300, 160, amp=400)
    out = agc.process(quiet)
    assert _rms(_to_float(out)) > 1.5 * _rms(_to_float(quiet))


def test_agc_attenuates_loud_caller():
    agc = AGC(target_rms=3000.0, adapt=1.0)
    loud = _sine(300, 160, amp=20000)
    out = agc.process(loud)
    assert _rms(_to_float(out)) < _rms(_to_float(loud))


def test_agc_leaves_silence_alone():
    agc = AGC()
    silence = _to_pcm16(np.zeros(160))
    out = agc.process(silence)
    assert _rms(_to_float(out)) == 0.0


# ─── EchoCanceller ───

def test_echo_canceller_reduces_pure_echo():
    """mic = a delayed, scaled copy of the reference (no near-end speech).
    After adaptation the residual should be far quieter than the mic."""
    ec = EchoCanceller(filter_len=128, mu=0.7)
    rng = np.random.default_rng(1)
    delay, gain = 5, 0.6
    prev_tail = np.zeros(200, dtype=np.float32)

    last_mic = last_out = None
    for _ in range(180):                       # frames to converge
        ref_f = rng.standard_normal(160).astype(np.float32) * 4000
        stream = np.concatenate([prev_tail, ref_f])
        echo = gain * stream[len(prev_tail) - delay: len(prev_tail) - delay + 160]
        prev_tail = ref_f[-200:]
        mic = _to_pcm16(echo)
        ref = _to_pcm16(ref_f)
        out = ec.process(mic, ref)
        last_mic, last_out = mic, out

    assert erle_db(last_mic, last_out) > 12.0   # >12 dB echo reduction


def test_echo_canceller_passes_near_end_when_no_reference():
    # No reference (silence) -> near-end speech passes through ~unchanged.
    ec = EchoCanceller()
    speech = _sine(300, 160, amp=5000)
    silence_ref = _to_pcm16(np.zeros(160))
    out = ec.process(speech, silence_ref)
    assert _rms(_to_float(out)) > 0.9 * _rms(_to_float(speech))


def test_echo_canceller_stable_under_double_talk():
    """Echo + strong near-end speech (double-talk). The Geigel detector must
    freeze adaptation so the filter weights stay bounded (no divergence) and the
    caller's speech survives."""
    ec = EchoCanceller(filter_len=128, mu=0.7)
    rng = np.random.default_rng(2)
    prev_tail = np.zeros(200, dtype=np.float32)
    near = _to_float(_sine(220, 160, amp=8000))   # loud caller

    weight_norms = []
    for _ in range(150):
        ref_f = rng.standard_normal(160).astype(np.float32) * 4000
        stream = np.concatenate([prev_tail, ref_f])
        echo = 0.6 * stream[len(prev_tail) - 5: len(prev_tail) - 5 + 160]
        prev_tail = ref_f[-200:]
        mic = _to_pcm16(echo + near)            # echo AND caller talking
        ec.process(mic, _to_pcm16(ref_f))
        weight_norms.append(float(np.linalg.norm(ec._w)))

    # Filter must not blow up (textbook NLMS diverges here).
    assert weight_norms[-1] < 3.0
    assert max(weight_norms) < 5.0


# ─── robustness ───

def test_empty_and_odd_inputs():
    for block in (NoiseGate(), AGC()):
        assert block.process(b"") == b""
    ec = EchoCanceller()
    assert ec.process(b"", b"") == b""


# ─── SpectralDenoiser ───

def _speech_like(n, amp=5000, sr=8000, seed=None):
    """Multi-tone 'speech' (fundamental + harmonics, telephony band)."""
    t = np.arange(n) / sr
    sig = (np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 440 * t)
           + 0.3 * np.sin(2 * np.pi * 880 * t))
    return (amp * sig / np.max(np.abs(sig))).astype(np.float32)


def _snr_db(clean: np.ndarray, noisy: np.ndarray) -> float:
    noise = noisy - clean
    return 10 * np.log10((np.mean(clean ** 2) + 1e-9) / (np.mean(noise ** 2) + 1e-9))


def test_denoiser_improves_snr_under_steady_noise():
    """Noise-only lead-in (learn the profile), then speech+noise: output SNR
    must be clearly better than input SNR."""
    rng = np.random.default_rng(7)
    dn = SpectralDenoiser()
    n_frames, hop = 120, 160

    # 40 frames of noise only — denoiser learns the noise spectrum.
    for _ in range(40):
        dn.process(_to_pcm16(rng.standard_normal(hop) * 500))

    # 80 frames of speech + the same steady noise.
    in_snr, out_snr = [], []
    delay = hop  # OLA introduces one hop of latency; compare accordingly
    prev_clean = np.zeros(hop, dtype=np.float32)
    for i in range(80):
        clean = _speech_like((i + 1) * hop, amp=4000)[i * hop:(i + 1) * hop]
        noise = rng.standard_normal(hop).astype(np.float32) * 500
        out = _to_float(dn.process(_to_pcm16(clean + noise)))
        if i >= 40:                       # steady state only
            in_snr.append(_snr_db(clean, clean + noise))
            out_snr.append(_snr_db(prev_clean, out))   # output lags one hop
        prev_clean = clean

    assert np.mean(out_snr) > np.mean(in_snr) + 3.0    # >3dB improvement


def test_denoiser_preserves_clean_speech_level():
    """With no noise learned, clean speech must pass at ~unity gain (validates
    the overlap-add + window normalization math)."""
    dn = SpectralDenoiser()
    sig = _speech_like(160 * 30, amp=5000)
    outs = []
    for i in range(30):
        outs.append(_to_float(dn.process(_to_pcm16(sig[i * 160:(i + 1) * 160]))))
    # skip warmup (OLA pipeline fill), compare steady-state RMS
    got = np.concatenate(outs[5:])
    want = sig[4 * 160:29 * 160]
    ratio = _rms(got) / _rms(want)
    assert 0.85 < ratio < 1.15


def test_denoiser_attenuates_pure_noise():
    rng = np.random.default_rng(3)
    dn = SpectralDenoiser()
    in_rms, out_rms = [], []
    for i in range(80):
        noise = rng.standard_normal(160).astype(np.float32) * 400
        out = _to_float(dn.process(_to_pcm16(noise)))
        if i >= 40:
            in_rms.append(_rms(noise))
            out_rms.append(_rms(out))
    assert np.mean(out_rms) < 0.4 * np.mean(in_rms)    # >8dB down


def test_denoiser_streaming_no_boundary_clicks():
    """Feeding one long signal in 160-sample chunks must produce a smooth
    output — no large sample-to-sample jumps at chunk boundaries."""
    dn = SpectralDenoiser()
    sig = _speech_like(160 * 20, amp=3000)
    outs = []
    for i in range(20):
        outs.append(_to_float(dn.process(_to_pcm16(sig[i * 160:(i + 1) * 160]))))
    joined = np.concatenate(outs[3:])
    max_jump = np.max(np.abs(np.diff(joined)))
    # a 220Hz tone at 3000 amp moves ~520/sample max; a click would be >>2000
    assert max_jump < 2000


def test_denoiser_empty_and_ragged_input():
    dn = SpectralDenoiser()
    assert dn.process(b"") == b""
    ragged = _to_pcm16(np.ones(100, dtype=np.float32) * 100)   # <1 hop: passthrough
    assert len(dn.process(ragged)) == len(ragged)


# ─── InboundDSP facade ───

def test_inbound_dsp_preserves_frame_length():
    dsp = InboundDSP(enable_gate=True, enable_agc=True, enable_echo=True)
    frame = _sine(300, 160, amp=4000)
    dsp.feed_reference(_to_pcm16(np.zeros(160)))
    out = dsp.process(frame)
    assert len(out) == len(frame)        # 160 samples in, 160 out


def test_inbound_dsp_all_disabled_is_identity():
    dsp = InboundDSP(enable_gate=False, enable_agc=False, enable_echo=False)
    frame = _sine(300, 160, amp=4000)
    assert dsp.process(frame) == frame


def test_inbound_dsp_feed_reference_noop_when_echo_off():
    dsp = InboundDSP(enable_gate=True, enable_agc=False, enable_echo=False)
    dsp.feed_reference(_to_pcm16(np.zeros(160)))   # must not raise / buffer
    assert dsp.echo is None
    assert len(dsp.process(_sine(300, 160, amp=4000))) == 320


def test_inbound_dsp_denoise_in_chain_reduces_noise():
    """Facade with denoise on: pure steady noise is strongly reduced."""
    rng = np.random.default_rng(11)
    dsp = InboundDSP(enable_gate=False, enable_agc=False, enable_echo=False,
                     enable_denoise=True)
    in_rms, out_rms = [], []
    for i in range(80):
        noise = _to_pcm16(rng.standard_normal(160) * 400)
        out = dsp.process(noise)
        if i >= 40:
            in_rms.append(_rms(_to_float(noise)))
            out_rms.append(_rms(_to_float(out)))
    assert np.mean(out_rms) < 0.4 * np.mean(in_rms)
    assert len(dsp.process(_to_pcm16(np.zeros(160)))) == 320   # frame length kept


def test_inbound_dsp_cancels_echo_end_to_end():
    """Full chain with echo on: feed reference frames, process mic=echo, and the
    output echo energy drops over time."""
    dsp = InboundDSP(enable_gate=False, enable_agc=False, enable_echo=True)
    rng = np.random.default_rng(3)
    prev = np.zeros(200, dtype=np.float32)
    last_mic = last_out = None
    for _ in range(180):
        ref_f = rng.standard_normal(160).astype(np.float32) * 4000
        stream = np.concatenate([prev, ref_f])
        echo = 0.6 * stream[len(prev) - 5: len(prev) - 5 + 160]
        prev = ref_f[-200:]
        dsp.feed_reference(_to_pcm16(ref_f))
        mic = _to_pcm16(echo)
        last_mic, last_out = mic, dsp.process(mic)
    assert erle_db(last_mic, last_out) > 10.0
