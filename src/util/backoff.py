"""Async retry with exponential backoff.

WebSocket setup fails transiently — DNS blips, TLS resets, a cold load balancer
on the first request of a call. Failing the entire call on the first hiccup is
needless; a few backed-off retries recover most transient faults. The clock is
injectable (``sleep``) so the policy is fully deterministic and unit-testable
without real sockets or wall-clock waits.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 0.2,
    max_delay: float = 2.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> T:
    """Call ``fn`` up to ``attempts`` times, backing off between failures.

    Delay before attempt N (1-indexed) is ``min(max_delay, base_delay*2**(N-1))``.
    Only ``exceptions`` are retried; anything else propagates immediately. The
    last exception is re-raised once attempts are exhausted.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except exceptions as exc:
            last = exc
            if attempt == attempts:
                break
            if on_retry is not None:
                on_retry(attempt, exc)
            await sleep(min(max_delay, base_delay * (2 ** (attempt - 1))))
    assert last is not None  # loop ran at least once
    raise last
