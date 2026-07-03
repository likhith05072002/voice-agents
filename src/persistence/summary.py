"""Post-call summarization.

After a call ends, one cheap LLM pass turns the transcript into a structured
record: a one-line summary, a normalized outcome, and caller sentiment. This is
what a business sees in their CRM/dashboard, and the ``outcome`` replaces the
generic "completed" so analytics reflect what actually happened.

Runs off the call's hot path (at teardown). The LLM is injected as a
``complete_json`` callable so it is testable without network.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from src.persistence.records import Turn

OUTCOMES = ("resolved", "booked", "info_provided", "abandoned", "follow_up", "other")

_SYSTEM = (
    "You summarize a phone call between a business voice agent and a caller. "
    "Return ONLY a JSON object with keys: "
    '"summary" (one sentence), '
    f'"outcome" (one of {", ".join(OUTCOMES)}), '
    '"sentiment" (positive, neutral, or negative). No other text.'
)


async def summarize_call(
    complete_json: Callable[[list[dict]], Awaitable[dict]],
    turns: list[Turn],
) -> dict:
    """Return ``{summary, outcome, sentiment}`` (subset present). Empty when
    there is nothing to summarize. Outcome is validated against ``OUTCOMES``."""
    if not turns:
        return {}
    transcript = "\n".join(f"{t.role}: {t.text}" for t in turns)
    data = await complete_json([
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": transcript},
    ])
    out = {}
    if isinstance(data.get("summary"), str):
        out["summary"] = data["summary"].strip()
    if data.get("outcome") in OUTCOMES:
        out["outcome"] = data["outcome"]
    if data.get("sentiment") in ("positive", "neutral", "negative"):
        out["sentiment"] = data["sentiment"]
    return out
