"""Bridge the resolved agent from the call-control webhook to the media stream.

Telnyx fires a webhook (``call.initiated``) that carries the dialed number, then
opens a SEPARATE media WebSocket that only carries the ``call_control_id``. This
registry stashes the resolved ``agent_id`` keyed by call id so the media stream
can pick it up.

In-process with TTL eviction — correct for a single worker. For multi-worker /
horizontally-scaled deployments, swap the dict for Redis behind this same API
(``put`` / ``pop``); nothing else changes. The clock is injected so eviction is
deterministic in tests.
"""

from __future__ import annotations


class CallRegistry:
    def __init__(self, ttl_s: float = 120.0):
        self.ttl_s = ttl_s
        self._m: dict[str, tuple[object, float]] = {}   # call_id -> (value, expiry)

    def put(self, call_id: str, value: object, *, now: float) -> None:
        """Stash any value (an agent id, or a dict of routing/caller info)."""
        if call_id:
            self._sweep(now)
            self._m[call_id] = (value, now + self.ttl_s)

    def pop(self, call_id: str, *, now: float) -> object | None:
        self._sweep(now)
        entry = self._m.pop(call_id, None)
        if entry is None:
            return None
        value, expiry = entry
        return value if expiry >= now else None

    def _sweep(self, now: float) -> None:
        expired = [k for k, (_, exp) in self._m.items() if exp < now]
        for k in expired:
            del self._m[k]

    def __len__(self) -> int:
        return len(self._m)
