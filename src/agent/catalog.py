"""Tool catalog — maps named tool sets to registries.

A business declares the tool sets it wants (``AgentConfig.tool_sets``); the
factory merges them into one registry for that agent. Registering a new vertical
(pharmacy, salon, clinic) is a one-line catalog addition — no call-path changes.
"""

from __future__ import annotations

from collections.abc import Callable

from src.agent.tools import ToolRegistry
from src.agent.demo_tools import build_demo_registry, build_assistant_registry

# name -> builder returning a ToolRegistry
TOOL_CATALOG: dict[str, Callable[[], ToolRegistry]] = {
    "jewellery": build_demo_registry,
    "assistant": build_assistant_registry,
}


def build_tools(names: list[str]) -> ToolRegistry | None:
    """Merge the named tool sets into one registry (None if none resolve).

    Carries prefetchers AND the filler hint across the merge — a tool set's
    live-data behaviour is not just its callable tools."""
    merged = ToolRegistry()
    found = False
    for name in names:
        builder = TOOL_CATALOG.get(name)
        if builder is None:
            continue
        reg = builder()
        for tool in reg.all():                    # merge tools from the set
            merged.register(tool)
            found = True
        for pf in getattr(reg, "_prefetchers", []):
            merged.prefetcher(pf)
        hint = getattr(reg, "_filler_hint", None)
        if hint is not None:
            merged.set_filler_hint(hint)
    return merged if found else None
