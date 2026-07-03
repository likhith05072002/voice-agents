"""Rate limiting + concurrent-session cap (abuse / overload protection).

Both are deterministic and clock-injectable so they unit-test without real time.
"""

from __future__ import annotations


class TokenBucket:
    """Classic token bucket. ``allow(now)`` returns True if a token is available,
    refilling continuously at ``refill_per_sec``. ``now`` is supplied by the
    caller (a monotonic clock) so tests are deterministic."""

    def __init__(self, capacity: float, refill_per_sec: float):
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        self.capacity = float(capacity)
        self.refill_per_sec = float(refill_per_sec)
        self._tokens = float(capacity)
        self._last: float | None = None

    def allow(self, now: float, cost: float = 1.0) -> bool:
        if self._last is None:
            self._last = now
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_per_sec)
        if self._tokens >= cost:
            self._tokens -= cost
            return True
        return False


class SessionLimiter:
    """Non-blocking cap on concurrent sessions. ``try_acquire`` rejects when at
    capacity; pair each success with ``release`` (use the context manager)."""

    def __init__(self, max_sessions: int):
        if max_sessions < 1:
            raise ValueError("max_sessions must be >= 1")
        self.max_sessions = max_sessions
        self._active = 0

    @property
    def active(self) -> int:
        return self._active

    def try_acquire(self) -> bool:
        if self._active >= self.max_sessions:
            return False
        self._active += 1
        return True

    def release(self) -> None:
        self._active = max(0, self._active - 1)
