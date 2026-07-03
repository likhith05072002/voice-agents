"""Call records — the durable artifact of every call.

A ``CallRecord`` captures who called which business, the full transcript, the
per-turn latency breakdowns, duration, and outcome. It is what powers analytics,
billing, QA, and compliance — none of which the ephemeral logs can do.

``CallRecorder`` accumulates a record cheaply *during* the call (append-only,
no I/O on the hot path) and is flushed to a ``CallStore`` once, at call end.
Clocks are injected so timing is deterministic in tests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict


@dataclass
class Turn:
    role: str           # "user" | "assistant"
    text: str
    t: float            # seconds since call start


@dataclass
class CallStats:
    agent_id: str | None
    total_calls: int
    avg_duration_s: float | None
    avg_perceived_ms: float | None
    by_outcome: dict[str, int]

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "total_calls": self.total_calls,
            "avg_duration_s": self.avg_duration_s,
            "avg_perceived_ms": self.avg_perceived_ms,
            "by_outcome": self.by_outcome,
        }


@dataclass
class CallRecord:
    call_id: str
    agent_id: str
    from_number: str = ""
    to_number: str = ""
    started_at: float = 0.0          # wall-clock epoch
    ended_at: float | None = None
    turns: list[Turn] = field(default_factory=list)
    metrics: list[dict] = field(default_factory=list)   # per-turn latency breakdowns
    outcome: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def duration_s(self) -> float | None:
        return None if self.ended_at is None else max(0.0, self.ended_at - self.started_at)

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    def avg_perceived_ms(self) -> float | None:
        vals = [m["perceived_ms"] for m in self.metrics if "perceived_ms" in m]
        return round(sum(vals) / len(vals), 1) if vals else None

    def to_row(self) -> dict:
        """Flat, JSON-friendly row for storage."""
        return {
            "call_id": self.call_id,
            "agent_id": self.agent_id,
            "from_number": self.from_number,
            "to_number": self.to_number,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_s": self.duration_s,
            "turn_count": self.turn_count,
            "avg_perceived_ms": self.avg_perceived_ms(),
            "outcome": self.outcome,
            "turns": json.dumps([asdict(t) for t in self.turns], ensure_ascii=False),
            "metrics": json.dumps(self.metrics, ensure_ascii=False),
            "metadata": json.dumps(self.metadata, ensure_ascii=False),
        }

    @classmethod
    def from_row(cls, row: dict) -> "CallRecord":
        return cls(
            call_id=row["call_id"],
            agent_id=row["agent_id"],
            from_number=row.get("from_number", ""),
            to_number=row.get("to_number", ""),
            started_at=row.get("started_at", 0.0),
            ended_at=row.get("ended_at"),
            turns=[Turn(**t) for t in json.loads(row.get("turns") or "[]")],
            metrics=json.loads(row.get("metrics") or "[]"),
            outcome=row.get("outcome", ""),
            metadata=json.loads(row.get("metadata") or "{}"),
        )


class CallRecorder:
    """Collects a CallRecord during one call (append-only, no I/O)."""

    def __init__(self, call_id: str, agent_id: str, *, from_number: str = "",
                 to_number: str = "", started_at: float = 0.0, clock=None):
        self.record = CallRecord(
            call_id=call_id, agent_id=agent_id,
            from_number=from_number, to_number=to_number, started_at=started_at,
        )
        # monotonic clock for relative turn timestamps (injected for tests)
        self._clock = clock or (lambda: 0.0)
        self._t0 = self._clock()

    def transcript(self, role: str, text: str) -> None:
        self.record.turns.append(Turn(role=role, text=text, t=round(self._clock() - self._t0, 3)))

    def metric(self, breakdown: dict) -> None:
        if breakdown:
            self.record.metrics.append(breakdown)

    def finalize(self, *, ended_at: float, outcome: str = "completed") -> CallRecord:
        self.record.ended_at = ended_at
        self.record.outcome = outcome
        return self.record
