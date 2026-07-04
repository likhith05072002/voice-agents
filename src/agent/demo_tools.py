"""Sample tools for the jewellery demo agent.

Demonstrates the function-calling surface end to end. Swap these handlers for
real backends (a live price feed, the CRM, a calendar) in production — the
registry/runner/engine wiring stays the same.
"""

from __future__ import annotations

import time

from src.agent.tools import ToolRegistry

# Last-known prices — FALLBACK ONLY, served if the live feed is unreachable.
GOLD_PRICE_INR = {24: 7800, 22: 7150, 18: 5850}

# Live spot cache: gold moves slowly enough that 15 minutes is fresh for a
# shop conversation, and it keeps the tool instant on repeat questions.
_gold_cache: dict = {"t": 0.0, "gram24": 0.0}


async def _live_gold_gram24_inr() -> float:
    """Live 24k INR/gram from free keyless feeds (XAU USD/oz x USDINR)."""
    if time.time() - _gold_cache["t"] < 900 and _gold_cache["gram24"]:
        return _gold_cache["gram24"]
    import httpx
    async with httpx.AsyncClient(timeout=6.0) as c:
        xau_usd_oz = (await c.get("https://api.gold-api.com/price/XAU")).json()["price"]
        usd_inr = (await c.get("https://open.er-api.com/v6/latest/USD")).json()["rates"]["INR"]
    gram24 = xau_usd_oz / 31.1035 * usd_inr
    _gold_cache.update(t=time.time(), gram24=gram24)
    return gram24


def build_demo_registry() -> ToolRegistry:
    reg = ToolRegistry()

    @reg.tool(
        "get_gold_price",
        "Get today's LIVE gold market price per gram in INR for a given karat.",
        {
            "type": "object",
            "properties": {"karat": {"type": "integer", "enum": [18, 22, 24]}},
            "required": ["karat"],
        },
    )
    async def get_gold_price(args: dict) -> dict:
        karat = int(args.get("karat", 22))
        try:
            gram24 = await _live_gold_gram24_inr()
            return {"karat": karat,
                    "price_per_gram_inr": round(gram24 * karat / 24),
                    "source": "live market rate",
                    "note": "spot market rate; retail adds GST and making charges"}
        except Exception as e:  # noqa: BLE001 — a dead feed must not kill the turn
            return {"karat": karat,
                    "price_per_gram_inr": GOLD_PRICE_INR.get(karat, GOLD_PRICE_INR[22]),
                    "source": "stale fallback (live feed unreachable)",
                    "error": str(e)}

    @reg.tool(
        "get_shop_hours",
        "Get the shop's opening hours.",
        {"type": "object", "properties": {}},
    )
    def get_shop_hours(args: dict) -> dict:
        return {"hours": "10 AM to 9 PM, daily"}

    return reg
