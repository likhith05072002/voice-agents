"""Tests for the paced batch dialer."""

import asyncio

from src.telephony.batch import BatchDialer


async def _wait_done(dialer, batch, timeout=2.0):
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if batch.done:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("batch did not finish")


async def test_batch_places_all_calls_and_reports():
    dialed = []

    async def dial(call):
        dialed.append(call["to"])
        return f"cc-{len(dialed)}"

    d = BatchDialer(dial, sleep=lambda s: asyncio.sleep(0))
    batch = d.start([{"to": "+1"}, {"to": "+2"}, {"to": "+3"}], pace_per_min=60)
    await _wait_done(d, batch)
    assert dialed == ["+1", "+2", "+3"]
    assert batch.placed == 3 and batch.failed == 0 and batch.done
    assert d.status(batch.batch_id) is batch


async def test_batch_paces_between_calls():
    sleeps = []

    async def dial(call):
        return "cc"

    async def fake_sleep(s):
        sleeps.append(s)

    d = BatchDialer(dial, sleep=fake_sleep)
    batch = d.start([{"to": "+1"}, {"to": "+2"}, {"to": "+3"}], pace_per_min=30)
    await _wait_done(d, batch)
    # 30/min -> 2s between calls; sleeps before call 2 and 3 only
    assert sleeps == [2.0, 2.0]


async def test_one_failure_does_not_kill_the_campaign():
    async def dial(call):
        if call["to"] == "+bad":
            raise RuntimeError("invalid number")
        return "cc"

    d = BatchDialer(dial, sleep=lambda s: asyncio.sleep(0))
    batch = d.start([{"to": "+1"}, {"to": "+bad"}, {"to": "+3"}], pace_per_min=0)
    await _wait_done(d, batch)
    assert batch.placed == 2 and batch.failed == 1
    errs = [r for r in batch.results if "error" in r]
    assert errs[0]["to"] == "+bad"


async def test_status_dict_shape():
    async def dial(call):
        return "cc"
    d = BatchDialer(dial, sleep=lambda s: asyncio.sleep(0))
    batch = d.start([{"to": "+1"}], pace_per_min=0)
    await _wait_done(d, batch)
    s = batch.to_dict()
    assert s["total"] == 1 and s["placed"] == 1 and s["done"] is True
    assert s["results"][0]["call_id"] == "cc"
