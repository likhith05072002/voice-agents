"""Sample tools for the jewellery demo agent.

Demonstrates the function-calling surface end to end. Swap these handlers for
real backends (a live price feed, the CRM, a calendar) in production — the
registry/runner/engine wiring stays the same.
"""

from __future__ import annotations

import json
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


# INDIAN market rates (what jeweller boards actually quote): international
# spot + import duty + local premium — measured ~15% above raw spot
# conversion. Fetched from live Indian sources via a search-grounded model,
# SANITY-CHECKED against spot (the search once returned silver below world
# spot — impossible), cached 30 min, warmed in the background at call start
# so no caller ever waits on the search.
_india_cache: dict = {"t": 0.0, "gold_24k": 0.0, "gold_22k": 0.0, "silver": 0.0}
_india_refreshing = False


async def _refresh_india_rates() -> None:
    global _india_refreshing
    if _india_refreshing:
        return
    _india_refreshing = True
    try:
        import os
        import re
        import json as _json
        import httpx
        from src.config import settings
        key = settings.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            return
        spot = await _live_spot_inr_per_gram()
        async with httpx.AsyncClient(timeout=25.0) as c:
            r = await c.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": "perplexity/sonar",
                      "messages": [{"role": "user", "content":
                          'What is today\'s gold rate in Hyderabad India per '
                          'gram for 22 carat and 24 carat, and silver rate per '
                          'gram in INR? Answer ONLY with JSON: {"gold_22k": N, '
                          '"gold_24k": N, "silver": N} in INR per gram.'}],
                      "max_tokens": 120})
            r.raise_for_status()
            txt = r.json()["choices"][0]["message"]["content"]
        m = re.search(r"\{[^}]+\}", txt)
        rates = _json.loads(m.group(0)) if m else {}

        def _sane(web: float, spot_v: float) -> bool:
            # Indian retail sits above spot (duty + premium), never far below.
            return spot_v > 0 and 0.95 <= web / spot_v <= 1.45

        g24 = float(rates.get("gold_24k") or 0)
        g22 = float(rates.get("gold_22k") or 0)
        ag = float(rates.get("silver") or 0)
        upd = {}
        if _sane(g24, spot["gold"]):
            upd["gold_24k"] = g24
            upd["gold_22k"] = g22 if _sane(g22, spot["gold"] * 22 / 24) else g24 * 22 / 24
        if _sane(ag, spot["silver"]):
            upd["silver"] = ag
        if upd:
            _india_cache.update(t=time.time(), **upd)
            logger_note = {k: round(v) for k, v in upd.items()}
            import structlog
            structlog.get_logger().info("india_rates.refreshed", **logger_note)
    except Exception:  # noqa: BLE001 — best-effort; spot fallback covers us
        pass
    finally:
        _india_refreshing = False


def warm_india_rates() -> None:
    """Fire-and-forget refresh if the cache is stale — call at call start so
    the rates are hot before the caller asks."""
    import asyncio
    if time.time() - _india_cache["t"] > 1800:
        try:
            asyncio.ensure_future(_refresh_india_rates())
        except RuntimeError:
            pass


# Intent regexes for deterministic prefetch (en/kn/hi/te/ta). Broad on
# purpose: a false-positive costs one cached lookup; a miss costs a
# hallucinated price.
import re as _re

_METAL_RE = _re.compile(
    r"gold|silver|ಗೋಲ್ಡ್|ಚಿನ್ನ|ಬೆಳ್ಳಿ|ಸಿಲ್ವರ್|सोना|सोने|चांदी|गोल्ड|सिल्वर|"
    r"బంగార|వెండి|గోల్డ్|தங்கம்|வெள்ளி", _re.IGNORECASE)
_QTY_RE = _re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:ಗ್ರಾಂ|grams?|g\b|ग्राम|గ్రామ|கிராம்|gm\b)", _re.IGNORECASE)
_KARAT_RE = _re.compile(r"\b(18|22|24)\b")
_AFFAIRS_RE = _re.compile(
    r"prime minister|president|chief minister|minister of|election|news|"
    r"who is (?:the )?(?:current|now)|ಪ್ರಧಾನ ?ಮಂತ್ರಿ|ರಾಷ್ಟ್ರಪತಿ|ಮುಖ್ಯಮಂತ್ರಿ|ಸುದ್ದಿ|"
    r"प्रधानमंत्री|राष्ट्रपति|मुख्यमंत्री|ముఖ్యమంత్రి|అధ్యక్ష|ప్రధాన|"
    r"பிரதமர்|ஜனாதிபதி|முதல்வர்", _re.IGNORECASE)


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

        warm_india_rates()               # keep the Indian-rate cache fresh
        per_gram = None
        # 1st choice: the INDIAN market rate — the number callers see on
        # jeweller boards and rate apps (includes duty + local premium).
        if time.time() - _india_cache["t"] < 7200:      # accept up to 2h old
            if metal == "gold" and _india_cache["gold_24k"]:
                base22, base24 = _india_cache["gold_22k"], _india_cache["gold_24k"]
                per_gram = {22: base22, 24: base24}.get(karat, base24 * karat / 24)
                source = "Indian market rate (live)"
            elif metal == "silver" and _india_cache["silver"]:
                per_gram = _india_cache["silver"]
                source = "Indian market rate (live)"
        if per_gram is None:
            # 2nd: international spot converted to INR (~10-15% below boards).
            try:
                spot = await _live_spot_inr_per_gram()
                base = spot[metal]
                per_gram = base * (karat / 24) if metal == "gold" else base
                source = "international spot rate (Indian retail runs a bit higher)"
            except Exception as e:  # noqa: BLE001 — a dead feed must not kill the turn
                base = FALLBACK_GRAM_INR[metal]
                per_gram = base * (karat / 24) if metal == "gold" else base
                source = f"stale fallback (live feeds unreachable: {e})"
        out = {"metal": metal,
               "price_per_gram_inr": round(per_gram),
               "grams": grams,
               "total_inr": round(per_gram * grams),
               "source": source,
               "note": "metal rate only; final bill adds making charges and GST"}
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

    @reg.prefetcher
    async def metal_rates_prefetch(text: str):
        """Any mention of gold/silver -> inject today's REAL rates so the model
        can never quote from memory, in any language, regardless of whether it
        would have chosen to call the tool."""
        if not _METAL_RE.search(text):
            return None
        g22 = json.loads(await reg.call("get_metal_price", {"metal": "gold", "karat": 22}))
        g24 = json.loads(await reg.call("get_metal_price", {"metal": "gold", "karat": 24}))
        ag = json.loads(await reg.call("get_metal_price", {"metal": "silver"}))
        line = (f"Today's live metal rates: gold 22 karat Rs.{g22['price_per_gram_inr']}"
                f" per gram, gold 24 karat Rs.{g24['price_per_gram_inr']} per gram, "
                f"silver Rs.{ag['price_per_gram_inr']} per gram "
                f"(source: {g22['source']}; metal rate only — making charges and GST extra).")
        m = _QTY_RE.search(text)
        if m:
            grams = float(m.group(1))
            k = _KARAT_RE.search(text)
            karat = int(k.group(1)) if k else 22
            per = {22: g22, 24: g24}.get(karat, g22)["price_per_gram_inr"]
            line += (f" For {grams:g} grams of {karat} karat gold that is "
                     f"Rs.{round(per * grams)} total.")
        return line

    @reg.prefetcher
    async def current_affairs_prefetch(text: str):
        """Current-events questions (leaders, elections, news) -> live web
        answer injected, so the model never answers public-figure questions
        from its (stale) training memory. Heard live: 'the US President is
        Joe Biden' in 2026."""
        if not _AFFAIRS_RE.search(text):
            return None
        result = json.loads(await reg.call("web_search", {"query": text[:200]}))
        if result.get("answer"):
            return f"Live web answer to the caller's question: {result['answer']}"
        return None

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
