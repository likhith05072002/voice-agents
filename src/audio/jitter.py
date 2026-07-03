"""Jitter buffer + packet-loss concealment for inbound media frames.

Telephony media here arrives over a TCP WebSocket (Telnyx), which is ordered and
loss-free at the transport, so these matter most when the upstream leg is lossy
(mobile networks dropping RTP before Telnyx, or a future UDP/RTP ingress). They
are therefore OPT-IN: a jitter buffer adds buffering latency that would hurt
barge-in if enabled needlessly.

  - ``PacketLossConcealer`` — on a missing frame, repeats the last good frame
    with a fade, decaying to silence after a few consecutive losses (avoids the
    click/gap that wrecks STT).
  - ``JitterBuffer`` — holds a small target of frames and releases one per pull
    at a steady cadence, concealing underruns and dropping on overflow.

Frames are PCM16 mono ``bytes``.
"""

from __future__ import annotations

from collections import deque

from src.audio.dsp import _to_float, _to_pcm16


def _silence(nbytes: int) -> bytes:
    return b"\x00" * nbytes


class PacketLossConcealer:
    def __init__(self, *, fade: float = 0.7, max_repeats: int = 5):
        self.fade = fade
        self.max_repeats = max_repeats
        self._last: bytes | None = None
        self._losses = 0

    def observe(self, frame: bytes) -> None:
        """Record a successfully received frame."""
        if frame:
            self._last = frame
            self._losses = 0

    def conceal(self) -> bytes:
        """Produce a concealment frame for a lost packet."""
        if self._last is None:
            return b""
        self._losses += 1
        if self._losses > self.max_repeats:
            return _silence(len(self._last))
        return _to_pcm16(_to_float(self._last) * (self.fade ** self._losses))


class JitterBuffer:
    def __init__(self, *, target: int = 2, capacity: int = 8,
                 plc: PacketLossConcealer | None = None):
        if target < 1 or capacity < target:
            raise ValueError("require 1 <= target <= capacity")
        self.target = target
        self.capacity = capacity
        self._q: deque[bytes] = deque()
        self._warmed = False
        self._plc = plc or PacketLossConcealer()

    def push(self, frame: bytes) -> None:
        self._q.append(frame)
        while len(self._q) > self.capacity:    # overflow -> drop oldest
            self._q.popleft()
        if len(self._q) >= self.target:
            self._warmed = True

    def pull(self) -> bytes:
        """Return the next frame, or a concealment frame on underrun. Returns
        b"" while still filling to ``target`` (warm-up)."""
        if not self._warmed:
            return b""
        if self._q:
            frame = self._q.popleft()
            self._plc.observe(frame)
            return frame
        # Warmed but empty -> genuine underrun: conceal rather than fall silent.
        return self._plc.conceal()

    def __len__(self) -> int:
        return len(self._q)
