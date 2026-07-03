"""Tool catalog — maps named tool sets to registries.

A business declares the tool sets it wants (``AgentConfig.tool_sets``); the
factory merges them into one registry for that agent. Registering a new vertical
(pharmacy, salon, clinic) is a one-line catalog addition — no call-path changes.
"""

from __future__ import annotations

from collections.abc import Callable

from src.agent.tools import ToolRegistry
from src.agent.demo_tools import build_demo_registry

# name -> builder returning a ToolRegistry
TOOL_CATALOG: dict[str, Callable[[], ToolRegistry]] = {
    "jewellery": build_demo_registry,
}


def build_tools(names: list[str]) -> ToolRegistry | None:
    """Merge the named tool sets into one registry (None if none resolve)."""
    merged = ToolRegistry()
    found = False
    for name in names:
        builder = TOOL_CATALOG.get(name)
        if builder is None:
            continue
        for tool in builder().all():              # merge tools from the set
            merged.register(tool)
            found = True
    return merged if found else None
