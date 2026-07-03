"""Tests for the async retry/backoff policy."""

import pytest

from src.util.backoff import retry_async


async def test_returns_on_first_success():
    calls = []

    async def fn():
        calls.append(1)
        return "ok"

    slept = []
    out = await retry_async(fn, sleep=lambda d: slept.append(d) or _noop())
    assert out == "ok"
    assert len(calls) == 1
    assert slept == []          # no retries -> no sleeps


async def _noop():
    return None


async def test_retries_then_succeeds_with_backoff():
    attempts = {"n": 0}

    async def fn():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError("transient")
        return "recovered"

    slept: list[float] = []

    async def fake_sleep(d):
        slept.append(d)

    out = await retry_async(fn, attempts=5, base_delay=0.2, sleep=fake_sleep)
    assert out == "recovered"
    assert attempts["n"] == 3
    # backed off before attempts 2 and 3: 0.2, 0.4
    assert slept == [0.2, 0.4]


async def test_raises_after_exhausting_attempts():
    async def fn():
        raise ConnectionError("always")

    async def fake_sleep(d):
        return None

    with pytest.raises(ConnectionError, match="always"):
        await retry_async(fn, attempts=3, sleep=fake_sleep)


async def test_does_not_retry_unlisted_exceptions():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        raise ValueError("not retryable")

    with pytest.raises(ValueError):
        await retry_async(fn, attempts=3, exceptions=(ConnectionError,),
                          sleep=lambda d: _noop())
    assert calls["n"] == 1       # tried once, no retry


async def test_max_delay_caps_backoff():
    slept: list[float] = []

    async def fn():
        raise ConnectionError("x")

    async def fake_sleep(d):
        slept.append(d)

    with pytest.raises(ConnectionError):
        await retry_async(fn, attempts=5, base_delay=1.0, max_delay=2.0, sleep=fake_sleep)
    # 1.0, 2.0, 2.0(capped), 2.0(capped) before the 5th (final) attempt
    assert slept == [1.0, 2.0, 2.0, 2.0]
