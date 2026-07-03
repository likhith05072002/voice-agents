"""Tool (function) calling for the voice agent.

A voice agent is only useful if it can *act* — quote today's price, check stock,
book an appointment. This is the OpenAI-compatible function-calling surface that
Sarvam's chat API supports: the model decides when to call a registered tool, we
execute it, feed the result back, and the model produces the spoken answer.

``ToolRegistry`` holds the tools and renders their JSON specs; ``Tool`` wraps one
callable (sync or async). Pure and unit-testable — no LLM or network here.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger()

Handler = Callable[[dict], Any] | Callable[[dict], Awaitable[Any]]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict          # JSON Schema for the arguments object
    handler: Handler

    def spec(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def __len__(self) -> int:
        return len(self._tools)

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def tool(self, name: str, description: str, parameters: dict) -> Callable[[Handler], Handler]:
        """Decorator form: @registry.tool("get_price", "...", {...})."""
        def deco(fn: Handler) -> Handler:
            self.register(Tool(name=name, description=description, parameters=parameters, handler=fn))
            return fn
        return deco

    def all(self) -> list[Tool]:
        """All registered tools (public iteration for merging/catalogs)."""
        return list(self._tools.values())

    def specs(self) -> list[dict]:
        return [t.spec() for t in self._tools.values()]

    async def call(self, name: str, args: dict) -> str:
        """Execute a tool and return its result as a string (JSON-encoded if the
        handler returns a non-string). Errors are returned as a tool result, not
        raised — the model should hear "the tool failed" and recover gracefully."""
        tool = self._tools.get(name)
        if tool is None:
            logger.warning("tool.unknown", name=name)
            return json.dumps({"error": f"unknown tool '{name}'"})
        try:
            result = tool.handler(args)
            if inspect.isawaitable(result):
                result = await result
        except Exception as e:  # noqa: BLE001 — surface as tool output, never crash the turn
            logger.warning("tool.error", name=name, error=str(e))
            return json.dumps({"error": str(e)})
        return result if isinstance(result, str) else json.dumps(result)


def parse_tool_calls(message: dict) -> list[dict]:
    """Extract OpenAI-style tool_calls from an assistant message (or [])."""
    return message.get("tool_calls") or []
