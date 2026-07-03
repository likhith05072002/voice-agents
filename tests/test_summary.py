"""Tests for post-call summarization."""

from src.persistence.summary import summarize_call
from src.persistence.records import Turn


async def test_summarizes_and_validates_fields():
    async def fake_complete_json(messages):
        # the transcript is in the user message
        assert "price" in messages[-1]["content"]
        return {"summary": "Caller asked the 22k gold price.",
                "outcome": "info_provided", "sentiment": "positive",
                "junk": "ignored"}

    out = await summarize_call(fake_complete_json,
                               [Turn("user", "what is the price?", 0.0),
                                Turn("assistant", "7150 rupees", 0.5)])
    assert out == {"summary": "Caller asked the 22k gold price.",
                   "outcome": "info_provided", "sentiment": "positive"}


async def test_invalid_outcome_and_sentiment_dropped():
    async def fake(messages):
        return {"summary": "x", "outcome": "made_up", "sentiment": "angry"}

    out = await summarize_call(fake, [Turn("user", "hi", 0.0)])
    assert out == {"summary": "x"}            # bad enums dropped, summary kept


async def test_empty_turns_skips_llm():
    async def fake(messages):
        raise AssertionError("LLM should not be called for an empty call")

    assert await summarize_call(fake, []) == {}


async def test_handles_empty_llm_response():
    async def fake(messages):
        return {}
    assert await summarize_call(fake, [Turn("user", "hi", 0.0)]) == {}
