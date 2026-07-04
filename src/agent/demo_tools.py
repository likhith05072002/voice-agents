"""Sample tools for the jewellery demo agent.

Demonstrates the function-calling surface end to end. Swap these handlers for
real backends (a live price feed, the CRM, a calendar) in production — the
registry/runner/engine wiring stays the same.
"""

from __future__ import annotations

import time

from src.agent.tools import ToolRegistry

TROY_OZ_G = 31.1035

# Last-known 24k gold / fine silver per-gram INR — FALLBACK ONLY, served if the
# live feed is unreachable so the agent still gives a number, never a refusal.
FALLBACK_GRAM_INR = {"gold": 11700, "silver": 168}

# Spot cache: metals move slowly enough that 15 minutes is fresh for a shop
# conversation, and it keeps the tool instant on repeat questions.
_spot_cache: dict = {"t": 0.0, "gold24": 0.0, "silver": 0.0}

# gold-api.com spot symbols.
_SYMBOL = {"gold": "XAU", "silver": "XAG"}


async def _live_spot_inr_per_gram() -> dict:
    """Live 24k gold + fine silver, INR per gram, from free keyless feeds."""
    if time.time() - _spot_cache["t"] < 900 and _spot_cache["gold24"]:
        return {"gold": _spot_cache["gold24"], "silver": _spot_cache["silver"]}
    import httpx
    async with httpx.AsyncClient(timeout=6.0) as c:
        xau = (await c.get("https://api.gold-api.com/price/XAU")).json()["price"]
        xag = (await c.get("https://api.gold-api.com/price/XAG")).json()["price"]
        usd_inr = (await c.get("https://open.er-api.com/v6/latest/USD")).json()["rates"]["INR"]
    gold24 = xau / TROY_OZ_G * usd_inr
    silver = xag / TROY_OZ_G * usd_inr
    _spot_cache.update(t=time.time(), gold24=gold24, silver=silver)
    return {"gold": gold24, "silver": silver}


def build_demo_registry() -> ToolRegistry:
    reg = ToolRegistry()

    @reg.tool(
        "get_metal_price",
        "Get today's LIVE market price in INR for gold or silver. Handles any "
        "quantity in grams and gold karat purity, and returns both the "
        "per-gram rate and the total for the requested quantity. Call this for "
        "ANY gold or silver price question — never quote a price from memory.",
        {
            "type": "object",
            "properties": {
                "metal": {"type": "string", "enum": ["gold", "silver"]},
                "karat": {"type": "integer", "enum": [18, 22, 24],
                          "description": "gold purity; ignored for silver"},
                "grams": {"type": "number",
                          "description": "quantity in grams (default 1)"},
            },
            "required": ["metal"],
        },
    )
    async def get_metal_price(args: dict) -> dict:
        metal = str(args.get("metal", "gold")).lower()
        if metal not in _SYMBOL:
            return {"error": f"unknown metal '{metal}'; we quote gold or silver"}
        karat = int(args.get("karat", 22)) if metal == "gold" else None
        try:
            grams = float(args.get("grams", 1) or 1)
        except (TypeError, ValueError):
            grams = 1.0
        try:
            spot = await _live_spot_inr_per_gram()
            base = spot[metal]
            per_gram = base * (karat / 24) if metal == "gold" else base
            source = "live market rate"
        except Exception as e:  # noqa: BLE001 — a dead feed must not kill the turn
            base = FALLBACK_GRAM_INR[metal]
            per_gram = base * (karat / 24) if metal == "gold" else base
            source = f"stale fallback (live feed unreachable: {e})"
        out = {"metal": metal,
               "price_per_gram_inr": round(per_gram),
               "grams": grams,
               "total_inr": round(per_gram * grams),
               "source": source,
               "note": "spot market rate; retail adds GST and making charges"}
        if karat is not None:
            out["karat"] = karat
        return out

    @reg.tool(
        "get_shop_hours",
        "Get the shop's opening hours.",
        {"type": "object", "properties": {}},
    )
    def get_shop_hours(args: dict) -> dict:
        return {"hours": "10 AM to 9 PM, daily"}

    import os
    from src.config import settings
    _or_key = settings.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if _or_key:
        @reg.tool(
            "web_search",
            "Search the live internet for current information: news, current "
            "events, festival dates, anything time-sensitive that is not in "
            "your FACTS. Returns a short factual answer.",
            {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )
        async def web_search(args: dict) -> dict:
            query = str(args.get("query", "")).strip()
            if not query:
                return {"error": "empty query"}
            import httpx
            try:
                async with httpx.AsyncClient(timeout=12.0) as c:
                    r = await c.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={"Authorization": f"Bearer {_or_key}"},
                        json={
                            # search-grounded model: answers from live web
                            "model": "perplexity/sonar",
                            "messages": [{
                                "role": "user",
                                "content": (f"Answer in 1-2 short factual "
                                            f"sentences: {query}")}],
                            "max_tokens": 150,
                        })
                    r.raise_for_status()
                    answer = r.json()["choices"][0]["message"]["content"]
                return {"answer": answer.strip()[:500]}
            except Exception as e:  # noqa: BLE001 — surface, never crash the turn
                return {"error": f"search unavailable: {e}"}

    return reg
