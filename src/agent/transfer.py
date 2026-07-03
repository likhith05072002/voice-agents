"""Human-handoff (call transfer) tool.

The defining receptionist capability: "let me connect you to the owner." The
LLM calls ``transfer_call`` with a named destination from the agent's configured
``transfer_numbers``; the handler *schedules* the transfer via the engine's
deferred-action hook so the caller hears the confirmation sentence before the
PSTN leg is bridged away. The Telnyx action is injected, so this is fully
testable without a network.
"""

from __future__ import annotations

import structlog

from src.agent.tools import Tool, ToolRegistry

logger = structlog.get_logger()


def build_transfer_tool(*, transfer_numbers: dict, defer, transfer_action) -> Tool:
    """Create the ``transfer_call`` tool.

    ``defer(action)``          — engine.defer_after_turn (runs post-playback)
    ``transfer_action(number)`` — async callable executing the actual transfer
    """
    destinations = sorted(transfer_numbers)

    def handler(args: dict):
        dest = str(args.get("destination", "")).strip().lower()
        number = transfer_numbers.get(dest)
        if not number:
            return {"error": f"unknown destination '{dest}'",
                    "available_destinations": destinations}

        async def _do_transfer():
            logger.info("transfer.executing", destination=dest)
            await transfer_action(number)

        defer(_do_transfer)
        logger.info("transfer.scheduled", destination=dest)
        return {"status": "transfer_scheduled", "destination": dest,
                "instruction": "Briefly tell the caller you are connecting them now."}

    return Tool(
        name="transfer_call",
        description=(
            "Transfer the caller to a human. Use when the caller asks for a "
            "person, or the request needs a human. Destinations: "
            + ", ".join(destinations)
        ),
        parameters={
            "type": "object",
            "properties": {
                "destination": {"type": "string", "enum": destinations,
                                "description": "Which person/department to connect to."},
            },
            "required": ["destination"],
        },
        handler=handler,
    )


def attach_transfer_tool(engine, agent, transfer_action) -> None:
    """Register the transfer tool on a built engine when the agent has
    transfer destinations configured."""
    if not agent.transfer_numbers:
        return
    registry = engine.tools or ToolRegistry()
    registry.register(build_transfer_tool(
        transfer_numbers=agent.transfer_numbers,
        defer=engine.defer_after_turn,
        transfer_action=transfer_action,
    ))
    engine.tools = registry
