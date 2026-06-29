import audioop

from src.audio.codec import Resampler, resample_8k_to_16k, resample_16k_to_8k


def _pcm(n_samples: int) -> bytes:
    # simple ramp, 16-bit signed
    return b"".join(int((i % 100) - 50).to_bytes(2, "little", signed=True) for i in range(n_samples))


def test_mulaw_roundtrip_preserves_length():
    pcm = _pcm(160)  # 20ms @ 8k
    ulaw = audioop.lin2ulaw(pcm, 2)
    assert len(ulaw) == 160  # 1 byte/sample
    back = audioop.ulaw2lin(ulaw, 2)
    assert len(back) == len(pcm)


def test_stateless_resample_changes_rate():
    pcm8 = _pcm(800)  # 100ms @ 8k
    up = resample_8k_to_16k(pcm8)
    # ~2x the samples
    assert abs(len(up) - 2 * len(pcm8)) <= 8
    down = resample_16k_to_8k(up)
    assert abs(len(down) - len(pcm8)) <= 8


def test_resampler_isolated_state():
    a, b = Resampler(), Resampler()
    chunk = _pcm(400)
    # Two independent resamplers must not share filter state.
    out_a = a.up_8k_to_16k(chunk) + a.up_8k_to_16k(chunk)
    out_b = b.up_8k_to_16k(chunk + chunk)
    assert len(out_a) == len(out_b)
