"""Inbound audio DSP for telephony: noise gate, AGC, and echo cancellation.

Telephony audio (8 kHz narrowband) is noisy and uneven, and the agent's own
voice can leak back through the PSTN hybrid and trip VAD — the "agent stops
randomly / interrupts itself" symptom. These blocks clean the inbound PCM16
stream *before* STT:

  - ``NoiseGate``  — tracks an adaptive noise floor and attenuates sub-floor
    frames, cutting background hiss/hum that triggers false VAD.
  - ``AGC``        — normalizes level toward a target RMS so quiet callers are
    heard and loud ones don't clip, improving STT accuracy.
  - ``EchoCanceller`` — an NLMS adaptive filter that subtracts the agent's
    outbound audio (reference) from the inbound mic, removing linear line/echo
    so the agent doesn't barge-in on itself.

All operate on PCM16 mono ``bytes`` and are per-call stateful (like ``Resampler``).
numpy-based and unit-tested on synthetic signals. The echo canceller's delay
alignment needs live tuning, so it is opt-in.
"""

from __future__ import annotations

import collections

import numpy as np

_I16_MAX = 32767
_I16_MIN = -32768


def _to_float(pcm16: bytes) -> np.ndarray:
    return np.frombuffer(pcm16, dtype=np.int16).astype(np.float32)


def _to_pcm16(x: np.ndarray) -> bytes:
    return np.clip(np.rint(x), _I16_MIN, _I16_MAX).astype(np.int16).tobytes()


def _rms(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(x * x)))


class NoiseGate:
    """Attenuate frames whose level sits at/below the adaptive noise floor."""

    def __init__(self, *, init_floor: float = 150.0, threshold_factor: float = 2.5,
                 attenuation: float = 0.1, adapt: float = 0.05):
        self._floor = init_floor
        self.threshold_factor = threshold_factor
        self.attenuation = attenuation
        self.adapt = adapt

    def process(self, pcm16: bytes) -> bytes:
        x = _to_float(pcm16)
        if x.size == 0:
            return pcm16
        rms = _rms(x)
        # When the frame is quiet, fold it into the noise-floor estimate.
        if rms < self._floor * self.threshold_factor:
            self._floor = (1 - self.adapt) * self._floor + self.adapt * rms
            return _to_pcm16(x * self.attenuation)
        return pcm16


class AGC:
    """Automatic gain control toward a target RMS, smoothed and gain-limited."""

    def __init__(self, *, target_rms: float = 3000.0, max_gain: float = 8.0,
                 min_gain: float = 0.25, adapt: float = 0.1, silence_rms: float = 80.0):
        self.target_rms = target_rms
        self.max_gain = max_gain
        self.min_gain = min_gain
        self.adapt = adapt
        self.silence_rms = silence_rms
        self._gain = 1.0

    def process(self, pcm16: bytes) -> bytes:
        x = _to_float(pcm16)
        if x.size == 0:
            return pcm16
        rms = _rms(x)
        if rms > self.silence_rms:   # don't pump up pure silence/noise
            desired = float(np.clip(self.target_rms / rms, self.min_gain, self.max_gain))
            self._gain = (1 - self.adapt) * self._gain + self.adapt * desired
        return _to_pcm16(x * self._gain)


class EchoCanceller:
    """NLMS adaptive echo canceller.

    Removes the part of the inbound mic signal that is a linear function of the
    recently-played outbound (reference) audio. ``mu`` is the step size (0<mu<2),
    ``filter_len`` the number of reference taps (echo memory in samples).
    """

    def __init__(self, *, filter_len: int = 128, mu: float = 0.5, eps: float = 1e-6,
                 leakage: float = 1e-4, dt_threshold: float = 2.0, peak_decay: float = 0.5):
        self.filter_len = filter_len
        self.mu = mu
        self.eps = eps
        # Leakage shrinks the weights slightly each update so stale taps decay and
        # the filter can't accumulate unbounded energy over a long call.
        self.leakage = leakage
        # Geigel double-talk threshold: if |mic| exceeds this * recent |ref| peak,
        # the near-end (caller) is talking over the echo — freeze adaptation so
        # the filter doesn't diverge and chew up the caller's speech.
        self.dt_threshold = dt_threshold
        self.peak_decay = peak_decay
        self._w = np.zeros(filter_len, dtype=np.float32)
        self._ref = np.zeros(filter_len, dtype=np.float32)   # newest sample at index 0
        self._ref_peak = 0.0

    def process(self, mic_pcm16: bytes, ref_pcm16: bytes) -> bytes:
        mic = _to_float(mic_pcm16)
        ref = _to_float(ref_pcm16)
        out = np.empty_like(mic)
        w, buf = self._w, self._ref
        # Per-frame reference peak (decayed) for the double-talk test — cheap and
        # stable vs recomputing a windowed max every sample.
        frame_peak = float(np.max(np.abs(ref))) if ref.size else 0.0
        ref_peak = max(frame_peak, self._ref_peak * self.peak_decay)
        self._ref_peak = ref_peak
        dt_level = self.dt_threshold * (ref_peak + self.eps)
        adapted = False
        for n in range(mic.size):
            buf[1:] = buf[:-1]
            buf[0] = ref[n] if n < ref.size else 0.0
            y = float(np.dot(w, buf))         # estimated echo
            e = mic[n] - y                    # error == echo-cancelled sample
            out[n] = e
            # Double-talk: near-end dominates -> don't adapt on this sample.
            if abs(mic[n]) <= dt_level:
                norm = float(np.dot(buf, buf)) + self.eps
                w += (self.mu * e / norm) * buf   # NLMS update
                adapted = True
        # Leakage applied once per frame (not per sample) — same slow weight
        # decay for long-run stability, far cheaper than an O(L) op every sample.
        if adapted and self.leakage:
            w *= (1.0 - self.leakage)
        return _to_pcm16(out)


class SpectralDenoiser:
    """Streaming spectral noise suppression (classical spectral subtraction).

    Unlike ``NoiseGate`` (which can only attenuate whole quiet frames), this
    removes steady background noise — fan, street hum, shop chatter — from
    UNDER the caller's speech, per frequency bin:

      1. Maintain a running noise magnitude spectrum, updated only on frames
         classified as non-speech (energy near the adaptive floor).
      2. For every frame, subtract ``oversubtract * noise_mag`` from the frame's
         magnitude spectrum, floored at ``spectral_floor * frame_mag`` to avoid
         the "musical noise" artifact of hard subtraction.
      3. Resynthesize with the original phase via overlap-add (Hann window,
         50% overlap), which keeps chunk boundaries click-free.

    Processes 20 ms / 160-sample hops at 8 kHz with a 320-sample window —
    one FFT per hop, pure numpy, real-time with large headroom. This is the
    classical baseline the neural suppressors (Krisp VIVA, DeepFilterNet)
    outperform; it is here because it needs no model, no license, and no new
    dependency. Swapping in a neural backend later only replaces this class.
    """

    HOP = 160                      # 20ms at 8kHz
    WIN = 320                      # 40ms Hann window, 50% overlap

    def __init__(self, *, oversubtract: float = 1.5, spectral_floor: float = 0.05,
                 noise_adapt: float = 0.1, speech_factor: float = 2.5,
                 init_floor: float = 150.0):
        self.oversubtract = oversubtract
        self.spectral_floor = spectral_floor
        self.noise_adapt = noise_adapt
        self.speech_factor = speech_factor
        self._energy_floor = init_floor
        # sqrt-Hann analysis + sqrt-Hann synthesis: their product is Hann, which
        # overlap-adds to unity at 50% overlap (COLA). Hann on both sides does
        # NOT (sums to 0.5..1.0 with ripple) — that halves and modulates gain.
        self._window = np.sqrt(np.hanning(self.WIN)).astype(np.float32)
        self._noise_mag: np.ndarray | None = None    # running noise spectrum
        self._in_buf = np.zeros(self.WIN - self.HOP, dtype=np.float32)   # last 160 in-samples
        self._ola_tail = np.zeros(self.WIN - self.HOP, dtype=np.float32) # overlap-add carry

    def process(self, pcm16: bytes) -> bytes:
        x = _to_float(pcm16)
        if x.size == 0:
            return pcm16
        out = np.empty_like(x)
        # Process in fixed HOP-sized chunks (telephony frames are 160-multiples;
        # a ragged tail is passed through untouched rather than mis-windowed).
        n_hops = x.size // self.HOP
        for i in range(n_hops):
            hop = x[i * self.HOP:(i + 1) * self.HOP]
            out[i * self.HOP:(i + 1) * self.HOP] = self._process_hop(hop)
        tail = n_hops * self.HOP
        if tail < x.size:
            out[tail:] = x[tail:]
        return _to_pcm16(out)

    def _process_hop(self, hop: np.ndarray) -> np.ndarray:
        frame = np.concatenate([self._in_buf, hop])          # WIN samples
        self._in_buf = frame[self.HOP:].copy()

        spec = np.fft.rfft(frame * self._window)
        mag = np.abs(spec)
        phase = np.angle(spec)

        # Track the noise spectrum on non-speech frames (adaptive energy floor).
        rms = _rms(hop)
        if rms < self._energy_floor * self.speech_factor:
            self._energy_floor = (1 - self.noise_adapt) * self._energy_floor \
                + self.noise_adapt * max(rms, 1.0)
            if self._noise_mag is None:
                self._noise_mag = mag.copy()
            else:
                self._noise_mag = (1 - self.noise_adapt) * self._noise_mag \
                    + self.noise_adapt * mag
        elif self._noise_mag is None:
            # Bootstrap: ambient noise louder than the initial floor would never
            # trigger learning (the floor can only adapt downward through the
            # gate above). Until a first profile exists, let the floor rise so
            # the threshold reaches the true ambient level within ~a second.
            self._energy_floor *= 1.02

        if self._noise_mag is not None:
            clean_mag = mag - self.oversubtract * self._noise_mag
            clean_mag = np.maximum(clean_mag, self.spectral_floor * mag)
        else:
            clean_mag = mag

        clean = np.fft.irfft(clean_mag * np.exp(1j * phase), n=self.WIN)
        clean = (clean * self._window).astype(np.float32)    # synthesis window

        # Overlap-add: with WIN = 2*HOP the carry is exactly one hop long, and
        # the sqrt-Hann pair is COLA at 50% overlap — no gain correction needed.
        out = clean[:self.HOP] + self._ola_tail
        self._ola_tail = clean[self.HOP:].copy()
        return out


class EchoProfile:
    """Per-call line-echo model at the 20ms-frame energy level.

    PSTN echoes the agent's own voice back with a delay (~100-700ms) far beyond
    a short NLMS window, and its loudness varies per call/voice — so neither
    sample-level AEC nor a static VAD threshold separates caller speech from
    echo reliably (measured: barge-in blocked ~1.5s on echo-heavy calls).

    This learns the delay and gain of the echo path from the GREETING (the
    caller is almost always silent while the agent introduces itself): it
    correlates the inbound RMS sequence against the outbound RMS history,
    picks the best-matching delay, and fits the gain. Afterwards,
    ``echo_estimate()`` predicts how loud the echo should be RIGHT NOW, and the
    VAD requires the caller to clearly exceed it.
    """

    MAX_DELAY = 50                      # frames (1s)
    LEARN_FRAMES = 200                  # initial fit after ~4s
    WINDOW = 400                        # rolling refit window (~8s)

    def __init__(self) -> None:
        self._out = collections.deque([0.0] * self.MAX_DELAY, maxlen=self.MAX_DELAY)
        self._pairs: collections.deque = collections.deque(maxlen=self.WINDOW)
        self._recent: collections.deque = collections.deque(maxlen=20)  # (in, out@delay)
        self._since_fit = 0
        self.delay: int | None = None
        self.gain = 0.0

    def feed_out(self, out_rms: float) -> None:
        """One outbound (agent) frame's loudness — called from the send path,
        so there is no read/reset race with the inbound thread."""
        self._out.appendleft(out_rms)

    def observe_in(self, in_rms: float) -> None:
        """One inbound frame's loudness. Keeps a rolling window and refits
        continuously — the greeting alone is NOT representative (pre-rendered
        audio differs in loudness from live TTS, which mis-trained the gain and
        let the agent's own echo truncate every answer on a real call)."""
        self._pairs.append((in_rms, tuple(self._out)))
        d = self.delay if self.delay is not None else 10
        self._recent.append((in_rms, self._out[d] if d < len(self._out) else 0.0))
        self._since_fit += 1
        if (self.delay is None and len(self._pairs) >= self.LEARN_FRAMES) or \
                (self.delay is not None and self._since_fit >= 150):
            self._fit()
            self._since_fit = 0

    def echo_correlation(self) -> float:
        """Normalized correlation between the inbound loudness envelope and the
        delayed outbound envelope over the last ~0.4s. Echo TRACKS the agent's
        own audio (corr near 1); genuine caller speech doesn't. This separates
        the two even when their energies overlap — thresholds alone cannot."""
        if len(self._recent) < 10:
            return 0.0
        ins = [i for i, _ in self._recent]
        outs = [o for _, o in self._recent]
        mi, mo = sum(ins) / len(ins), sum(outs) / len(outs)
        num = sum((i - mi) * (o - mo) for i, o in self._recent)
        di = sum((i - mi) ** 2 for i in ins) ** 0.5
        do = sum((o - mo) ** 2 for o in outs) ** 0.5
        if di < 1e-6 or do < 1e-6:
            return 0.0
        return num / (di * do)

    # Back-compat shim for tests/tools that fed both sides at once.
    def observe(self, out_rms: float, in_rms: float) -> None:
        self.feed_out(out_rms)
        self.observe_in(in_rms)

    def bump_gain(self) -> None:
        """Called when a candidate recovered with no transcript — evidence the
        'speech' was actually our own echo. Learn from the mistake."""
        self.gain = min(1.0, max(self.gain, 0.05) * 1.3)

    def _fit(self) -> None:
        best_d, best_score = 0, -1.0
        for d in range(2, self.MAX_DELAY):
            score = sum(i * o[d] for i, o in self._pairs)
            if score > best_score:
                best_d, best_score = d, score
        ratios = [i / o[best_d] for i, o in self._pairs if o[best_d] > 1500 and i > 50]
        self.delay = best_d
        if ratios:
            new_gain = sorted(ratios)[len(ratios) // 2]
            # Monotonic within a call: false-recovery bumps must never be
            # decayed back down by a refit (they were paid for with a stutter).
            self.gain = min(1.0, max(new_gain, self.gain))

    def echo_estimate(self) -> float:
        """Expected echo loudness for the CURRENT inbound frame."""
        if self.delay is None:
            # Not learned yet: assume a conservative worst case (recent max out).
            return 0.35 * max(self._out)
        return self.gain * self._out[self.delay]

    def speech_threshold(self, floor: float = 400.0) -> float:
        """Caller speech must clearly exceed the predicted echo. 1.8x keeps an
        80% margin over the prediction while catching soft onsets ("Sorry...")
        ~100-150ms sooner than 2.2x did on real calls."""
        return max(floor, 1.8 * self.echo_estimate())


class InboundDSP:
    """Per-call inbound cleanup chain: echo-cancel (optional) → noise gate → AGC.

    Operates on PCM16 8 kHz mono — the format ``main.py`` produces right after
    decoding the telephony frame, before resampling to 16 kHz for STT. When echo
    cancellation is enabled, the outbound audio is pushed via ``feed_reference``
    so the canceller can subtract the agent's own voice from the mic.
    """

    def __init__(self, *, enable_gate: bool = True, enable_agc: bool = True,
                 enable_echo: bool = False, enable_denoise: bool = False):
        self.gate = NoiseGate() if enable_gate else None
        self.agc = AGC() if enable_agc else None
        self.echo = EchoCanceller() if enable_echo else None
        self.denoise = SpectralDenoiser() if enable_denoise else None
        self._ref: collections.deque[bytes] = collections.deque(maxlen=50)

    def feed_reference(self, pcm16_8k: bytes) -> None:
        """Hand the canceller a frame of outbound (agent) audio as PCM16 8 kHz."""
        if self.echo is not None:
            self._ref.append(pcm16_8k)

    def process(self, pcm16_8k: bytes) -> bytes:
        x = pcm16_8k
        if self.echo is not None:
            ref = self._ref.popleft() if self._ref else b"\x00\x00" * (len(x) // 2)
            if len(ref) < len(x):                     # pad short reference
                ref = ref + b"\x00\x00" * ((len(x) - len(ref)) // 2)
            x = self.echo.process(x, ref[:len(x)])
        if self.denoise is not None:
            x = self.denoise.process(x)               # spectral noise removal
        if self.gate is not None:
            x = self.gate.process(x)
        if self.agc is not None:
            x = self.agc.process(x)
        return x


def erle_db(mic_pcm16: bytes, cleaned_pcm16: bytes) -> float:
    """Echo Return Loss Enhancement (dB): how much echo energy was removed.
    Positive = reduction. Used by tests/benchmarks to quantify cancellation."""
    mic = _to_float(mic_pcm16)
    cleaned = _to_float(cleaned_pcm16)
    mic_p = float(np.mean(mic * mic)) + 1e-9
    res_p = float(np.mean(cleaned * cleaned)) + 1e-9
    return 10.0 * np.log10(mic_p / res_p)
