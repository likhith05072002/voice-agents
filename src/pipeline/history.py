"""Bounded conversation-context selection.

The engine keeps the whole call's history for the transcript, but the LLM should
only see a recent, budgeted window — long histories blow up latency and cost.
``select_context`` returns the trailing messages within a char + message budget,
without orphaning a ``tool`` result from the assistant ``tool_call`` it answers
(the chat API rejects a tool message with no preceding tool_call).
"""

from __future__ import annotations


def _msg_len(m: dict) -> int:
    return len(str(m.get("content", "")))


def select_context(messages: list[dict], *, max_chars: int = 6000,
                   max_messages: int = 20) -> list[dict]:
    """Most-recent window of ``messages`` within both budgets.

    Trims from the front by message count then character budget, and never lets
    the window start on a ``tool`` message (which would be an orphaned result)."""
    if not messages:
        return []

    window = list(messages[-max_messages:])
    total = sum(_msg_len(m) for m in window)
    while len(window) > 1 and total > max_chars:
        total -= _msg_len(window.pop(0))

    # Don't begin on an orphan tool result.
    while window and window[0].get("role") == "tool":
        window.pop(0)
    return window


def prune(messages: list[dict], *, max_keep: int = 100) -> list[dict]:
    """Cap retained history so a very long call can't grow memory without bound.
    Keeps the most recent ``max_keep`` messages."""
    if len(messages) <= max_keep:
        return messages
    return messages[-max_keep:]
