"""Website -> voice agent onboarding (the ElevenLabs-receptionist flow).

POST /onboard/research takes a company website URL (plus an optional
"how should it behave" description), researches the business, and returns a
DRAFT AgentConfig: production-template persona, greeting, knowledge facts,
suggested language. The frontend lets the owner edit the draft, then creates
it via the existing POST /agents — and they can talk to it seconds later over
/web-call.

Research = two sources merged:
  1. the website itself (fetched + stripped to text), and
  2. a live web-search summary (OpenRouter sonar) when a key is configured —
     catches facts the homepage doesn't state (locations, reviews, hours).
"""

from __future__ import annotations

import re

import httpx
import structlog

logger = structlog.get_logger()

# The persona skeleton that survived weeks of live-call hardening; the LLM
# fills business specifics, we keep the guardrails verbatim.
_PERSONA_TEMPLATE = """You are {assistant_name}, the phone receptionist for {business_name}. {personality_line}

THIS IS A LIVE PHONE CALL. Speak naturally and briefly: 1-2 short sentences unless the caller asks for details, then explain thoroughly. Say numbers and prices as natural speech, not digits.

LANGUAGE: Always reply in the SAME language the caller uses.

{task_section}

STAY ACCURATE: Only state facts about the business that appear in your FACTS. Do not invent products, services, prices, discounts or offers. If you do not know something, say you will have a staff member confirm and offer to take a callback number — never guess. If the caller's words seem garbled, politely ask them to repeat.

BOUNDARIES: {boundaries_line} For complaints or anything you cannot handle, offer a callback from the team.

STYLE: Answer the caller's question directly in your first sentence. Once the call is underway, do not greet again or reintroduce the business unless asked who you are."""


def _strip_html(html: str, cap: int = 6000) -> str:
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text[:cap]


async def _fetch_site(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True,
                                 headers={"User-Agent": "Mozilla/5.0 (SonusLabs onboarding)"}) as c:
        r = await c.get(url)
        r.raise_for_status()
        return _strip_html(r.text)


async def _search_summary(company_hint: str, openrouter_key: str) -> str:
    if not openrouter_key:
        return ""
    try:
        async with httpx.AsyncClient(timeout=25.0) as c:
            r = await c.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {openrouter_key}"},
                json={"model": "perplexity/sonar",
                      "messages": [{"role": "user", "content":
                          f"Summarize key business facts about {company_hint}: what they "
                          f"do, products/services, locations, hours, anything a phone "
                          f"receptionist should know. 8 short bullet points max."}],
                      "max_tokens": 400})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"][:2500]
    except Exception as e:  # noqa: BLE001 — search is a bonus source
        logger.warning("onboard.search_failed", error=str(e))
        return ""


async def research_website(*, url: str, description: str, complete_json,
                           openrouter_key: str = "") -> dict:
    """Research a business and return a draft agent config dict.

    ``complete_json(messages) -> dict`` is the LLM callable (injected so this
    module stays testable without network)."""
    site_text = ""
    try:
        site_text = await _fetch_site(url)
    except Exception as e:  # noqa: BLE001
        logger.warning("onboard.fetch_failed", url=url, error=str(e))
    web_facts = await _search_summary(url, openrouter_key)
    if not site_text and not web_facts:
        raise ValueError("could not fetch the website or find it online")

    draft = await complete_json([
        {"role": "system", "content":
            "You design AI phone receptionists. From the research below, return "
            "STRICT JSON with keys: business_name, industry, assistant_name (an "
            "Indian first name fitting the brand), personality_line (one sentence: "
            "tone for this receptionist), task_section (2-4 sentences starting "
            "with an UPPERCASE label like 'BOOKINGS:' describing the caller tasks "
            "this business needs: bookings, orders, quotes, support...), "
            "boundaries_line (one sentence of industry-appropriate limits, e.g. "
            "no medical/legal/financial advice, no fake discounts), greeting_text "
            "(one warm phone greeting naming the business), knowledge_docs (array "
            "of 8-12 short standalone facts a receptionist needs: what they do, "
            "products/services, hours, locations, policies — ONLY facts present "
            "in the research), suggested_language (BCP-47 like en-IN or hi-IN)."},
        {"role": "user", "content":
            f"OWNER'S DESCRIPTION OF DESIRED BEHAVIOUR:\n{description or '(none given)'}\n\n"
            f"WEBSITE TEXT:\n{site_text[:5000]}\n\nWEB RESEARCH:\n{web_facts}"},
    ])
    if not draft or not draft.get("business_name"):
        raise ValueError("research produced no usable business profile")

    system_prompt = _PERSONA_TEMPLATE.format(
        assistant_name=draft.get("assistant_name", "Asha"),
        business_name=draft["business_name"],
        personality_line=draft.get("personality_line", "You are warm, professional and helpful."),
        task_section=draft.get("task_section", "GENERAL: Answer questions about the business and take messages."),
        boundaries_line=draft.get("boundaries_line", "Never promise prices or commitments you cannot confirm."),
    )
    agent_id = re.sub(r"[^a-z0-9]+", "-", draft["business_name"].lower()).strip("-")[:40]
    return {
        "agent_id": agent_id or "new-agent",
        "name": draft["business_name"],
        "industry": draft.get("industry", ""),
        "language": draft.get("suggested_language", "en-IN"),
        "voice": "neha",
        "system_prompt": system_prompt,
        "greeting_text": draft.get("greeting_text",
                                   f"Hello, thank you for calling {draft['business_name']}!"),
        "knowledge_docs": [d for d in (draft.get("knowledge_docs") or []) if isinstance(d, str)][:12],
        "enable_rag": True,
        "enable_tools": False,
    }
