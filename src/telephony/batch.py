"""Batch outbound calling — paced campaign dialing.

An AI receptionist product needs campaigns: appointment reminders, payment
follow-ups, festival offers. ``BatchDialer`` places a list of calls at a
configured pace (calls/minute) so the campaign neither floods the trunk nor
exceeds the concurrent-session capacity, and tracks per-batch progress.

The dial function and sleep are injected, so pacing and error isolation are
fully unit-testable without a network or a real clock.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()


@dataclass
class BatchStatus:
    batch_id: str
    total: int
    placed: int = 0
    failed: int = 0
    done: bool = False
    results: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id, "total": self.total, "placed": self.placed,
            "failed": self.failed, "done": self.done, "results": self.results,
        }


class BatchDialer:
    def __init__(self, dial, *, sleep=asyncio.sleep, max_batches: int = 100):
        """``dial(call: dict) -> str`` places one call and returns its id.
        Batches beyond ``max_batches`` evict the oldest finished status."""
        self._dial = dial
        self._sleep = sleep
        self._batches: dict[str, BatchStatus] = {}
        self._max_batches = max_batches

    def status(self, batch_id: str) -> BatchStatus | None:
        return self._batches.get(batch_id)

    def start(self, calls: list[dict], *, pace_per_min: float = 10.0) -> BatchStatus:
        """Begin a paced batch in the background; returns its (live) status."""
        batch = BatchStatus(batch_id=uuid.uuid4().hex[:12], total=len(calls))
        self._evict_if_needed()
        self._batches[batch.batch_id] = batch
        asyncio.create_task(self._run(batch, list(calls), pace_per_min))
        return batch

    async def _run(self, batch: BatchStatus, calls: list[dict], pace_per_min: float) -> None:
        interval = 60.0 / pace_per_min if pace_per_min > 0 else 0.0
        for i, call in enumerate(calls):
            if i > 0 and interval:
                await self._sleep(interval)
            try:
                call_id = await self._dial(call)
                batch.placed += 1
                batch.results.append({"to": call.get("to", ""), "call_id": call_id})
            except Exception as e:  # noqa: BLE001 — one bad number must not kill the campaign
                batch.failed += 1
                batch.results.append({"to": call.get("to", ""), "error": str(e)})
                logger.warning("batch.call_failed", to=call.get("to", ""), error=str(e))
        batch.done = True
        logger.info("batch.done", batch_id=batch.batch_id,
                    placed=batch.placed, failed=batch.failed)

    def _evict_if_needed(self) -> None:
        while len(self._batches) >= self._max_batches:
            for bid, st in list(self._batches.items()):
                if st.done:
                    del self._batches[bid]
                    break
            else:
                break   # nothing finished to evict; allow overflow rather than drop live
