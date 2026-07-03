"""Tests for bounded conversation-context selection."""

from src.pipeline.history import select_context, prune


def _msgs(n):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(n)]


def test_empty():
    assert select_context([]) == []


def test_limits_by_message_count():
    out = select_context(_msgs(50), max_messages=10, max_chars=10_000)
    assert len(out) == 10
    assert out[-1]["content"] == "m49"        # keeps the most recent


def test_limits_by_char_budget():
    msgs = [{"role": "user", "content": "x" * 100} for _ in range(20)]
    out = select_context(msgs, max_messages=20, max_chars=350)
    # 350 / 100 -> ~3 messages fit
    assert len(out) <= 4
    assert sum(len(m["content"]) for m in out) <= 400


def test_does_not_start_on_orphan_tool_message():
    msgs = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "tool_call_id": "1", "content": "result"},
        {"role": "assistant", "content": "answer"},
    ]
    # Force a tight window that would otherwise begin on the tool message.
    out = select_context(msgs, max_messages=2, max_chars=10_000)
    assert out[0]["role"] != "tool"


def test_keeps_at_least_one_message_even_over_budget():
    msgs = [{"role": "user", "content": "x" * 10_000}]
    out = select_context(msgs, max_chars=100)
    assert len(out) == 1


def test_prune_caps_history():
    msgs = _msgs(150)
    out = prune(msgs, max_keep=100)
    assert len(out) == 100
    assert out[-1]["content"] == "m149"


def test_prune_noop_under_cap():
    msgs = _msgs(10)
    assert prune(msgs, max_keep=100) is msgs
