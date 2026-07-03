"""Tool-resolution loop.

Runs the call→execute→call cycle until the model stops requesting tools, then
returns the augmented message list. The engine streams the FINAL answer from
those messages (so the spoken reply already reflects the tool results). The LLM
is injected as a ``complete`` callable so this is fully testable without network.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

import structlog

from src.agent.tools import ToolRegistry, parse_tool_calls

logger = structlog.get_logger()

# complete(messages, tools) -> (content, tool_calls)
Complete = Callable[[list[dict], list[dict]], Awaitable[tuple[str | None, list[dict]]]]


async def resolve_tools(
    complete: Complete,
    messages: list[dict],
    registry: ToolRegistry,
    *,
    max_rounds: int = 3,
) -> tuple[list[dict], str | None]:
    """Run the tool-resolution loop. Returns ``(messages, answer)``.

    ``answer`` is the content of the terminating (no-tool-calls) completion — the
    caller speaks it directly, avoiding a second streaming LLM call. It is
    ``None`` only if the round budget is exhausted (caller falls back to
    streaming on the augmented messages). Bounded by ``max_rounds``.
    """
    if len(registry) == 0:
        return messages, None
    msgs = list(messages)
    specs = registry.specs()
    for round_no in range(1, max_rounds + 1):
        content, tool_calls = await complete(msgs, specs)
        if not tool_calls:
            return msgs, (content or "")
        msgs.append({"role": "assistant", "content": content or "", "tool_calls": tool_calls})
        for tc in parse_tool_calls({"tool_calls": tool_calls}):
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            result = await registry.call(name, args)
            logger.info("tool.called", name=name, round=round_no,
                        result=result[:80].encode("ascii", "replace").decode())
            msgs.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "name": name,
                "content": result,
            })
    logger.warning("tool.max_rounds_exhausted", rounds=max_rounds)
    return msgs, None
