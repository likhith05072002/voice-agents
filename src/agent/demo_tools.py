"""Sample tools for the jewellery demo agent.

Demonstrates the function-calling surface end to end. Swap these handlers for
real backends (a live price feed, the CRM, a calendar) in production — the
registry/runner/engine wiring stays the same.
"""

from __future__ import annotations

from src.agent.tools import ToolRegistry

# Static demo data (replace with a live feed).
GOLD_PRICE_INR = {24: 7800, 22: 7150, 18: 5850}


def build_demo_registry() -> ToolRegistry:
    reg = ToolRegistry()

    @reg.tool(
        "get_gold_price",
        "Get today's gold price per gram in INR for a given karat.",
        {
            "type": "object",
            "properties": {"karat": {"type": "integer", "enum": [18, 22, 24]}},
            "required": ["karat"],
        },
    )
    def get_gold_price(args: dict) -> dict:
        karat = int(args.get("karat", 22))
        return {"karat": karat, "price_per_gram_inr": GOLD_PRICE_INR.get(karat, GOLD_PRICE_INR[22])}

    @reg.tool(
        "get_shop_hours",
        "Get the shop's opening hours.",
        {"type": "object", "properties": {}},
    )
    def get_shop_hours(args: dict) -> dict:
        return {"hours": "10 AM to 9 PM, daily"}

    return reg
