"""Tests for output moderation, token-bucket rate limiting, and session cap."""

import pytest

from src.safety.moderation import moderate, is_blocked
from src.util.ratelimit import TokenBucket, SessionLimiter


# ─── moderation ───

def test_profanity_is_detected_and_blocking():
    cats = moderate("this is shit quality")
    assert "profanity" in cats
    assert is_blocked(cats) is True


def test_clean_text_has_no_categories():
    assert moderate("22 carat gold is 7150 rupees per gram") == set()


def test_pii_flagged_but_not_blocking():
    cats = moderate("call me at 9876543210 or me@example.com")
    assert "pii" in cats
    assert is_blocked(cats) is False        # don't gag legit info


def test_price_numbers_are_not_pii():
    assert "pii" not in moderate("the price is 7800 for 10 grams")


# ─── token bucket ───

def test_token_bucket_allows_up_to_capacity_then_blocks():
    tb = TokenBucket(capacity=2, refill_per_sec=0)
    assert tb.allow(now=0.0) is True
    assert tb.allow(now=0.0) is True
    assert tb.allow(now=0.0) is False       # capacity exhausted


def test_token_bucket_refills_over_time():
    tb = TokenBucket(capacity=1, refill_per_sec=1.0)
    assert tb.allow(now=0.0) is True
    assert tb.allow(now=0.5) is False       # not enough refilled
    assert tb.allow(now=1.0) is True        # 1 token back after 1s


def test_token_bucket_caps_at_capacity():
    tb = TokenBucket(capacity=2, refill_per_sec=10.0)
    tb.allow(now=0.0)
    # long idle shouldn't let it exceed capacity
    assert tb.allow(now=100.0) is True
    assert tb.allow(now=100.0) is True
    assert tb.allow(now=100.0) is False


# ─── session limiter ───

def test_session_limiter_caps_concurrency():
    sl = SessionLimiter(max_sessions=2)
    assert sl.try_acquire() is True
    assert sl.try_acquire() is True
    assert sl.try_acquire() is False        # at cap
    sl.release()
    assert sl.try_acquire() is True         # freed one


def test_session_limiter_release_floor():
    sl = SessionLimiter(max_sessions=1)
    sl.release()                            # underflow guarded
    assert sl.active == 0


def test_session_limiter_validates():
    with pytest.raises(ValueError):
        SessionLimiter(0)


# ─── engine integration ───

import asyncio  # noqa: E402

from src.pipeline.turn_engine import TurnEngine  # noqa: E402
from src.services.stt.sarvam import TranscriptEvent  # noqa: E402
from src.services.llm.sarvam import SentenceEvent  # noqa: E402
from src.safety.guard import DEFAULT_REFUSAL  # noqa: E402


class _STT:
    def __init__(self): self.q = asyncio.Queue()
    async def get_event(self): return await self.q.get()


class _ProfaneLLM:
    async def generate_sentences(self, messages, queue):
        await queue.put(SentenceEvent(text="That is shit quality honestly.",
                                      is_first=True, timestamp=0.0))
        await queue.put(None)
        return "x"
    def cancel(self): ...


class _OkLLM:
    async def generate_sentences(self, messages, queue):
        await queue.put(SentenceEvent(text="ok. ", is_first=True, timestamp=0.0))
        await queue.put(None)
        return "ok. "
    def cancel(self): ...


class _TTS:
    def __init__(self): self._p = []
    async def reset(self): self._p = []
    async def send_text(self, t): self._p = [b"\x01\x00" * 160, None]
    async def flush(self): ...
    async def get_audio(self): return self._p.pop(0) if self._p else None


async def test_engine_blocks_profanity_with_refusal():
    engine = TurnEngine(stt=_STT(), llm=_ProfaneLLM(), tts=_TTS(),
                        send_media=lambda f: _noop(),
                        system_prompt="s", greeting_text="", frame_pace_s=0,
                        enable_safety=True)
    run = asyncio.create_task(engine.run())
    await engine.stt.q.put(TranscriptEvent(text="what do you think",
                                           is_final=True, language="en", timestamp=0.0))
    await _wait(lambda: any(h["role"] == "assistant" for h in engine.history))
    await engine.stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)
    answer = next(h for h in engine.history if h["role"] == "assistant")["content"]
    assert answer == DEFAULT_REFUSAL


async def test_engine_rate_limits_turns():
    tb = TokenBucket(capacity=1, refill_per_sec=0)
    engine = TurnEngine(stt=_STT(), llm=_OkLLM(), tts=_TTS(),
                        send_media=lambda f: _noop(),
                        system_prompt="s", greeting_text="", frame_pace_s=0,
                        turn_bucket=tb)
    run = asyncio.create_task(engine.run())
    await engine.stt.q.put(TranscriptEvent(text="one", is_final=True, language="en", timestamp=0.0))
    await _wait(lambda: sum(h["role"] == "assistant" for h in engine.history) == 1)
    # second turn should be dropped by the bucket (no new assistant message)
    await engine.stt.q.put(TranscriptEvent(text="two", is_final=True, language="en", timestamp=0.0))
    await asyncio.sleep(0.1)
    assert sum(h["role"] == "assistant" for h in engine.history) == 1
    await engine.stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)


async def _noop():
    return None


async def _wait(pred, timeout=2.0):
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if pred():
            return True
        await asyncio.sleep(0.005)
    raise AssertionError("condition not met")
