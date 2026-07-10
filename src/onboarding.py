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

import ipaddress
import re
import socket
from urllib.parse import urlparse

import httpx
import structlog

logger = structlog.get_logger()

# The persona skeleton. ONLY the live-call MECHANICS are fixed (brevity,
# same-language, natural numbers, no re-greeting) — everything about how the
# agent BEHAVES is role-driven: its goal, its task steps, and crucially HOW it
# carries the call. The old template hardcoded receptionist-passive behaviour
# ("answer the caller's question first", "offer a callback") which made every
# agent react-and-wait — a debt-recovery agent would accept "I'll pay later"
# and hang up. Now {objective_line} + {drive_line} let a collections/sales/
# survey agent LEAD and hold the line, while a receptionist stays reactive.
_PERSONA_TEMPLATE = """You are {assistant_name}, {role_line} {personality_line}

THIS IS A LIVE PHONE CALL. Speak naturally and briefly — 1-2 short sentences at a time unless the other person asks for detail. Say numbers and prices as natural speech, not digits.

LANGUAGE: Always reply in the SAME language the other person uses.

YOUR GOAL ON THIS CALL: {objective_line}

{task_section}

HOW YOU CARRY THE CALL: {drive_line}

STAY REAL: Only state specifics that appear in your FACTS — never invent amounts, dates, offers, prices, or commitments. If you genuinely do not know something, say you will get it confirmed rather than guess. If the other person's words seem garbled, politely ask them to repeat. Once the call is underway, do not re-greet or reintroduce yourself unless asked who you are.

BOUNDARIES: {boundaries_line}"""

_DEFAULT_ROLE = "the phone receptionist for {business_name}."
_DEFAULT_OBJECTIVE = ("Understand what the caller needs, help them, and capture "
                      "anything the business should follow up on.")
_DEFAULT_DRIVE = ("Let the caller lead. Answer their question directly in your "
                  "first sentence, then offer the next helpful step.")


def _clean_role_line(role: str, assistant_name: str) -> str:
    """The template already writes 'You are <name>, ' — models often return
    the FULL sentence anyway (seen live: 'You are Rohit, You are Rohit, a
    debt recovery agent...'). Strip any leading 'You are [<name>,]' echo."""
    role = (role or "").strip()
    role = re.sub(rf"^you\s+are\s+(?:{re.escape(assistant_name)}\s*[,—-]?\s*)?",
                  "", role, flags=re.IGNORECASE)
    if role and role[-1] not in ".!?":
        role += "."
    return role


async def enhance_prompt(*, description: str, business_name: str = "",
                         complete_json) -> str:
    """One-liner behaviour description -> full production system prompt.

    The user says WHAT ("behave like a debt recovery agent"); the LLM writes
    the role/tasks/boundaries; the live-call guardrail skeleton is applied
    verbatim — an enhanced prompt is never allowed to lose the brevity /
    same-language / answer-first rules that keep calls usable."""
    if not (description or "").strip():
        raise ValueError("describe how the agent should behave")
    draft = await complete_json([
        {"role": "system", "content": _ENHANCE_INSTRUCTION},
        {"role": "user", "content":
            f"BUSINESS NAME: {business_name or '(not given)'}\n"
            f"DESIRED BEHAVIOUR:\n{description.strip()[:2000]}"},
    ])
    if not draft or not draft.get("role_line"):
        raise ValueError("could not enhance that description — try rephrasing")
    return _build_prompt(draft)


# The keys the persona-designer LLM must return, and how to phrase them. Shared
# by the enhancer and the website researcher so both produce agenda-capable
# agents. drive_line is what makes a collections/sales agent LEAD instead of
# waiting to be asked.
_JSON_KEYS = (
    "assistant_name (an Indian first name fitting the role), "
    "role_line (finishes 'You are <name>, ...' in one line, faithful to the "
    "description), personality_line (one sentence: tone), "
    "objective_line (the ONE concrete goal of this call — e.g. for collections "
    "'secure a payment today or a firm date and amount'; for a receptionist "
    "'help the caller and capture their need'; for sales 'book a demo'), "
    "task_section (2-5 sentences, each starting with an UPPERCASE label like "
    "'OPENING:' 'COLLECTION:' 'CLOSING:' — the concrete steps this agent works "
    "through on the call), "
    "drive_line (1-2 sentences on HOW the agent carries the conversation. For "
    "PROACTIVE roles — collections, sales, surveys, appointment-setting, "
    "follow-ups — it must LEAD: briefly acknowledge the person, then steer "
    "firmly back to the goal, and NEVER accept a vague brush-off like 'later' "
    "or 'I'll pay when I can' — instead press for specifics and offer concrete "
    "options, only closing once there is a real commitment or a clear refusal. "
    "For REACTIVE roles — a receptionist, help desk — let the caller lead and "
    "answer helpfully. Always stay within boundaries.), "
    "boundaries_line (one sentence of role-appropriate limits — for sensitive "
    "roles like debt recovery include legal conduct: no threats, no "
    "harassment, respect do-not-call requests), "
    "greeting_text (one phone greeting matching the ROLE)"
)

_ENHANCE_INSTRUCTION = (
    "You design AI phone agents of ANY kind — receptionists, debt recovery, "
    "sales, surveys, appointment-setters. Expand the owner's behaviour "
    "description into STRICT JSON with keys: " + _JSON_KEYS + ". Match the "
    "owner's intent exactly: if they want a firm collections agent, make it "
    "genuinely persistent (within legal limits), not a passive one. Do NOT "
    "invent business facts.")


def _build_prompt(draft: dict) -> str:
    name = draft.get("assistant_name", "Asha")
    return _PERSONA_TEMPLATE.format(
        assistant_name=name,
        role_line=_clean_role_line(draft.get("role_line", ""), name)
        or "the phone assistant.",
        personality_line=draft.get("personality_line", "You are professional and clear."),
        objective_line=draft.get("objective_line") or _DEFAULT_OBJECTIVE,
        task_section=draft.get("task_section", "GENERAL: Handle the call as described."),
        drive_line=draft.get("drive_line") or _DEFAULT_DRIVE,
        boundaries_line=draft.get("boundaries_line",
                                  "Never promise anything you cannot confirm."),
    )


def _strip_html(html: str, cap: int = 6000) -> str:
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text[:cap]


def _assert_public_host(url: str) -> None:
    """SSRF guard: the research fetcher must only reach the public internet.

    Any signed-up user controls this URL — without the check they could make
    the server probe itself (127.0.0.1:<port>), the home LAN the Pi sits on
    (192.168.x.x routers, printers), or cloud metadata endpoints. Every
    resolved address must be public."""
    host = urlparse(url).hostname or ""
    if not host:
        raise ValueError("invalid URL")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ValueError("could not resolve that website") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise ValueError("that address is not reachable from here")


async def _fetch_site(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    # Follow redirects MANUALLY so every hop is re-validated — a public URL
    # 302ing to http://192.168.1.1/ must die at the redirect, not get fetched.
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=False,
                                 headers={"User-Agent": "Mozilla/5.0 (SonusLabs onboarding)"}) as c:
        for _ in range(4):
            _assert_public_host(url)
            r = await c.get(url)
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                url = str(httpx.URL(url).join(r.headers["location"]))
                continue
            r.raise_for_status()
            return _strip_html(r.text)
        raise ValueError("too many redirects")


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
    # Reject internal/localhost targets up front — otherwise a blocked fetch
    # falls through to the search step and hallucinates an "agent" from the
    # URL string. Normalize scheme first so the host check sees the real host.
    probe = url if url.startswith(("http://", "https://")) else "https://" + url
    try:
        _assert_public_host(probe)
    except ValueError:
        raise ValueError("please enter a public website address") from None

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
            "You design AI phone agents (receptionists, debt recovery, order "
            "desks, surveys — whatever the owner asks for). From the research "
            "below, return STRICT JSON with keys: business_name, industry, "
            + _JSON_KEYS + ", knowledge_docs (array of 8-12 short standalone "
            "facts this agent needs — ONLY facts present in the research), "
            "suggested_language (BCP-47 like en-IN or hi-IN). "
            "PRIORITY ORDER when sources disagree: (1) the owner's "
            "description of desired behaviour ALWAYS wins — it defines the "
            "agent's role AND how proactively it drives the call, even if the "
            "website suggests a receptionist; (2) the WEBSITE TEXT is ground "
            "truth about the business; (3) WEB RESEARCH is a hint only — if "
            "it describes a different company than the website text (same "
            "name, different business), IGNORE the web research entirely."},
        {"role": "user", "content":
            f"OWNER'S DESCRIPTION OF DESIRED BEHAVIOUR (this wins):\n"
            f"{description or '(none given — default to a receptionist)'}\n\n"
            f"WEBSITE TEXT (ground truth):\n{site_text[:5000]}\n\n"
            f"WEB RESEARCH (hint only):\n{web_facts}"},
    ])
    if not draft or not draft.get("business_name"):
        raise ValueError("research produced no usable business profile")

    if not draft.get("role_line"):
        draft["role_line"] = _DEFAULT_ROLE.format(business_name=draft["business_name"])
    system_prompt = _build_prompt(draft)
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
