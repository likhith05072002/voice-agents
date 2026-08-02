"""Voice Agent — telephony entrypoint.

FastAPI app that:
  1. Answers Telnyx calls and starts a bidirectional media stream (PCMU 8kHz).
  2. Bridges the media WebSocket to the STT/LLM/TTS pipeline.
  3. Delegates all turn-taking and barge-in to :class:`TurnEngine`.

The brain lives in ``src.pipeline.turn_engine``; this module is just I/O glue.
"""

import asyncio
import audioop
import base64
import dataclasses
import json
import os
import re
import secrets
import time
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.config import settings
from src.audio.codec import Resampler
from src.audio.dsp import InboundDSP, EchoProfile
from src.security.telnyx import verify_telnyx_signature
from src.services.stt.sarvam import SarvamSTTClient
from src.services.llm.sarvam import SarvamLLMClient
from src.services.tts.sarvam import SarvamTTSClient, KNOWN_VOICES
from src.services.tts.inworld import InworldTTSClient
from src.services.tts.elevenlabs import ElevenLabsTTSClient
from src.pipeline.filler import FillerPlayer
from src.util.ratelimit import TokenBucket, SessionLimiter
from src.tenancy.agents import agent_from_settings
from src.tenancy.store import AgentStore, load_agents_json
from src.tenancy.call_registry import CallRegistry
from src.tenancy.factory import build_engine, agent_eagerness
from src.tenancy.agents import AgentConfig
from src.tenancy.repository import SqliteAgentRepository, AgentManager
from src.persistence.records import CallRecorder
from src.persistence.store import SqliteCallStore
from src.persistence.summary import summarize_call
from src.telephony.telnyx import TelnyxClient
from src.telephony.batch import BatchDialer
from src.integrations.webhooks import post_event
from src.agent.transfer import attach_transfer_tool
from src.agent import kb_i18n
from src.agent.demo_tools import warm_india_rates
from src.testing.scenario import load_scenario, list_scenarios
from src.testing.runner import TestRun

structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
logger = structlog.get_logger()

# uvloop: 2-4x faster event-loop primitives on Linux (the Raspberry Pi target).
# Matters here because the 20ms frame pump + three websockets live or die on
# scheduler latency. Windows dev boxes fall through to the default loop.
try:
    import uvloop
    uvloop.install()
    logger.info("uvloop.enabled")
except ImportError:
    pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Accounts mode: bring up the Postgres pool + schema BEFORE hydrating,
    # since the agent repo reads from it.
    if settings.database_url:
        from src.accounts import db as _accounts_db
        await _accounts_db.connect(settings.database_url)
        logger.info("accounts.connected")
    # Load persisted agents into the live store on boot.
    n = await _agent_manager.hydrate()
    logger.info("agents.hydrated", count=n, total=len(_agent_store))

    # Warm multilingual KB translations for every agent in the background —
    # content-hash cached, so only new/changed docs ever hit the translate API.
    async def _warm_kbs():
        for a in _agent_store.all():
            if a.knowledge_docs:
                try:
                    await kb_i18n.warm(a.knowledge_docs,
                                       api_key=settings.sarvam_api_key)
                except Exception as e:  # noqa: BLE001 — warm is best-effort
                    logger.warning("kb_i18n.warm_failed", agent=a.agent_id,
                                   error=str(e))
    warm_task = asyncio.create_task(_warm_kbs())
    # Pre-render the voice + language previews so the demo's play buttons are instant.
    samples_task = asyncio.create_task(_warm_voice_samples())
    lang_samples_task = asyncio.create_task(_warm_lang_samples())
    yield
    warm_task.cancel()
    samples_task.cancel()
    lang_samples_task.cancel()
    if settings.database_url:
        from src.accounts import db as _accounts_db
        await _accounts_db.close()


# docs_url=None: /docs belongs to the SonusLabs developer portal (the React
# SPA route) — FastAPI's default Swagger UI would shadow it on the single
# origin. The OpenAPI autodocs are disabled entirely (the portal is curated).
app = FastAPI(title="Voice Agent", version="0.8.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)

# The SonusLabs frontend is a separate React app (different origin in dev and
# prod) — without CORS every fetch dies in preflight.
# Accounts mode uses an HttpOnly session cookie, and browsers FORBID
# wildcard-origin + credentials — so when CORS_ALLOW_ORIGINS is set we switch
# to an exact allowlist with credentials. Legacy mode keeps the old wildcard.
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
_cors_origins = [o.strip().rstrip("/") for o in
                 (settings.cors_allow_origins or "").split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Accounts (Phase 4): Google sign-in + workspaces. Routes exist in both modes;
# they 404/no-op cleanly when DATABASE_URL is unset (legacy mode).
from src.accounts import auth as _auth  # noqa: E402
from src.accounts import repo as _accounts_repo  # noqa: E402
from src.accounts import billing as _billing  # noqa: E402
from src.accounts import apikeys as _apikeys  # noqa: E402
from src.accounts.billing_routes import router as _billing_router  # noqa: E402
from src.accounts.admin_routes import router as _admin_router  # noqa: E402
app.include_router(_auth.router)
app.include_router(_billing_router)
app.include_router(_admin_router)


def _accounts_on() -> bool:
    return bool(settings.database_url)


# Agents callable from the public landing orb WITHOUT a session (the demo).
_PUBLIC_DEMO_AGENTS = {s.strip() for s in
                       (settings.public_demo_agents or "").split(",") if s.strip()}


def _origin_allowed(origin: str, allowed: list[str]) -> bool:
    """Website-widget gate: is the embedding page's Origin on the agent's
    allowlist? '*' opts into any origin (the owner's explicit choice)."""
    if not origin or not allowed:
        return False
    o = origin.rstrip("/").lower()
    for a in allowed:
        a = (a or "").strip().rstrip("/").lower()
        if a == "*" or a == o:
            return True
    return False


_sessions = SessionLimiter(settings.max_concurrent_sessions)
_call_registry = CallRegistry()

# Active BILLED calls per workspace (in-process, like SessionLimiter). Guards
# the prepaid wallet against the concurrency race: N simultaneous calls each
# capped at the full balance could spend N× it — so each new call's budget is
# the balance divided across the calls already running.
_billed_active: dict[str, int] = {}


def _billed_start(ws: str) -> int:
    n = _billed_active.get(ws, 0) + 1
    _billed_active[ws] = n
    return n


def _billed_end(ws: str) -> None:
    n = _billed_active.get(ws, 0) - 1
    if n <= 0:
        _billed_active.pop(ws, None)
    else:
        _billed_active[ws] = n


def _sonuslabs_demo_agent() -> AgentConfig:
    """The landing-page demo agent, defined in code so it can never fall back
    to the settings default persona (which is the jewellery demo — heard live:
    the orb greeted callers with 'Nama Srinivasa Jewellery'). A DB/agents-file
    'sonuslabs' still wins if one is added later (hydration overwrites)."""
    return AgentConfig(
        agent_id="sonuslabs",
        name="SonusLabs",
        language="en-IN",
        voice="neha",
        system_prompt=(
            "You are Neha, the AI receptionist demo for SonusLabs — a voice-AI "
            "platform that answers business phones in English, Hindi, Telugu, "
            "Kannada, Tamil and more. RULES, in order: "
            "(1) ANSWER the caller's question directly with real information "
            "in your very first sentence. NEVER reply to a question with "
            "another question. NEVER repeat or rephrase the caller's words "
            "back to them. NEVER start a reply with filler like 'అవును', "
            "'हाँ', 'सरे', or 'Yes,'. "
            "(2) Reply in the SAME language the caller is speaking — but if "
            "they ASK you to speak some language, switch to it immediately. "
            "(3) Keep answers SHORT: 1-2 sentences, never more. "
            "(4) General-knowledge questions (like what Google does) are a "
            "chance to show off: answer them briefly and correctly, then you "
            "may steer back to SonusLabs. "
            "(5) If asked who you are, reply with EXACTLY this one sentence "
            "and stop: 'I'm Neha, the SonusLabs AI receptionist demo.' "
            "About SonusLabs: an AI receptionist that answers business calls "
            "24/7, books appointments, answers customer questions, switches "
            "languages mid-call, and can even speak in the owner's own cloned "
            "voice; pay-as-you-go from Rs. 3.5 per minute, no app to install, "
            "works with your existing number. If you truly don't know "
            "something, say so plainly and suggest sonuslabs.ai."
        ),
        greeting_text=(
            "Hi! I'm Neha from SonusLabs — your AI receptionist demo. "
            "Ask me anything, in any language!"
        ),
        # Live web answers: "do some research on X" / news / prices trigger a
        # deterministic prefetch (assistant tool set) — heard live: the demo
        # said "I don't have information on that" to an explicit research ask.
        enable_tools=True,
        tool_sets=["assistant"],
        enable_rag=False,
        # ~0.95 reads calmer/more human (same tuning note as the phone agents);
        # heard live: a caller asked the clone "why do you speak so fast?".
        voice_pace=0.95,
        # sarvam-30b ignores "1-2 sentences" often enough (5-sentence identity
        # ramble heard live) that the demo gets the deterministic engine cap.
        max_reply_sentences=2,
    )


def _build_agent_store() -> AgentStore:
    """Default agent from global settings (single-tenant compat), plus the
    built-in landing demo agent, plus any agents declared in AGENTS_FILE
    (multi-tenant)."""
    store = AgentStore([agent_from_settings(settings)], default_id="default")
    store.add(_sonuslabs_demo_agent())
    if settings.agents_file:
        try:
            for a in load_agents_json(settings.agents_file):
                store.add(a)
            logger.info("agents.loaded", count=len(store))
        except Exception as e:  # noqa: BLE001 — never block startup on a bad file
            logger.error("agents.load_failed", error=str(e))
    return store


_agent_store = _build_agent_store()
# Accounts mode: agents + calls persist in Postgres (tenant-scoped). Legacy:
# the zero-infra SQLite stores. Postgres objects use the shared pool that
# lifespan connects before hydrate, so building them at import is safe.
if settings.database_url:
    from src.tenancy.pg_repository import PostgresAgentRepository
    from src.persistence.pg_store import PostgresCallStore
    _call_store = PostgresCallStore() if settings.enable_persistence else None
    _agent_repo = PostgresAgentRepository()
else:
    _call_store = SqliteCallStore(settings.calls_db_path) if settings.enable_persistence else None
    _agent_repo = SqliteAgentRepository(settings.agents_db_path)
_telnyx = TelnyxClient(settings.telnyx_api_key)
_outbound_bucket = (
    TokenBucket(settings.max_outbound_per_min, settings.max_outbound_per_min / 60.0)
    if settings.max_outbound_per_min > 0 else None
)
_agent_manager = AgentManager(_agent_store, _agent_repo)


async def _batch_dial(call: dict) -> str:
    return await _place_outbound(to=call["to"], agent_id=call.get("agent_id"),
                                 from_number=call.get("from_number"),
                                 context=call.get("context"))


_batch_dialer = BatchDialer(_batch_dial)


def _check_admin(request: Request):
    if settings.admin_api_key and request.headers.get("x-api-key") != settings.admin_api_key:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return None


async def _console_ctx(request: Request) -> tuple[dict | None, str | None, JSONResponse | None]:
    """Authorization context for console endpoints.

    Accounts mode -> (user, workspace_id, None) or (None, None, error): a valid
    session cookie AND an X-Workspace-Id the user is a member of are required.
    Legacy mode -> (None, None, admin-check result): old shared-key behavior.
    """
    if not _accounts_on():
        return None, None, _check_admin(request)
    user = await _auth.current_user(request)
    if user is None:
        return None, None, JSONResponse({"error": "unauthorized"}, status_code=401)
    ws, err = await _auth.request_workspace(request, user)
    if err is not None:
        return None, None, err
    return user, ws, None


def _ws_agents(ws: str) -> list[AgentConfig]:
    return [a for a in _agent_manager.list() if a.workspace_id == ws]


# Field-size ceilings for client-supplied agent config. Generous for real use,
# hostile to abuse (accounts mode lets any signed-up user write these).
_AGENT_LIMITS = {
    "name": 120, "system_prompt": 24_000, "greeting_text": 1_000,
    "idle_reprompt_text": 500, "webhook_url": 500, "industry": 120,
}


def _validate_agent_body(body: dict) -> str | None:
    """Returns an error string, or None when the payload is acceptable."""
    for field, cap in _AGENT_LIMITS.items():
        v = body.get(field)
        if isinstance(v, str) and len(v) > cap:
            return f"{field} too long (max {cap} chars)"
    docs = body.get("knowledge_docs")
    if isinstance(docs, list):
        if len(docs) > 60:
            return "too many knowledge_docs (max 60)"
        for d in docs:
            if isinstance(d, str) and len(d) > 4_000:
                return "a knowledge doc is too long (max 4000 chars)"
    for lst, cap in (("phone_numbers", 20), ("tool_sets", 20), ("embed_origins", 20)):
        v = body.get(lst)
        if isinstance(v, list) and len(v) > cap:
            return f"too many {lst} (max {cap})"
    return None


def _unique_agent_id(base: str) -> str:
    """Globally-unique slug: keeps the flat in-memory index / WS routing keys
    while letting every workspace name their agent whatever they want."""
    slug = re.sub(r"[^a-z0-9]+", "-", (base or "agent").lower()).strip("-")[:40] or "agent"
    if _agent_store.get(slug) is None:
        return slug
    import secrets as _secrets
    while True:
        cand = f"{slug}-{_secrets.token_hex(2)}"
        if _agent_store.get(cand) is None:
            return cand


# Platform/internal agents that are NOT a customer's workspace agents, so they
# are hidden from the console listing (the fallback 'default' and the public
# landing-demo assistant 'sonuslabs'). They still resolve for calls — only the
# console list is filtered. Accounts mode replaces this with tenant scoping.
_CONSOLE_HIDDEN_AGENTS = {"default", "sonuslabs"}


@app.get("/agents")
async def list_agents(request: Request):
    user, ws, err = await _console_ctx(request)
    if err is not None:
        return err
    if ws is not None:
        return {"agents": [a.to_dict() for a in _ws_agents(ws)]}
    return {"agents": [a.to_dict() for a in _agent_manager.list()
                       if a.agent_id not in _CONSOLE_HIDDEN_AGENTS]}


@app.get("/agents/{agent_id}")
async def get_agent(agent_id: str, request: Request):
    user, ws, err = await _console_ctx(request)
    if err is not None:
        return err
    a = _agent_manager.get(agent_id)
    if a is None or (ws is not None and a.workspace_id != ws):
        return JSONResponse({"error": "not found"}, status_code=404)
    return a.to_dict()


@app.post("/onboard/research")
async def onboard_research(request: Request):
    """Website URL (+ optional behaviour description) -> draft agent config.
    The frontend edits the draft, then creates it via POST /agents."""
    if _accounts_on():
        # Research burns LLM tokens — signed-in users only (no workspace needed:
        # nothing is written yet).
        if await _auth.current_user(request) is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    url = (body.get("website_url") or "").strip()
    if not url:
        return JSONResponse({"error": "website_url required"}, status_code=400)
    from src.onboarding import research_website
    # A full draft (persona fields + 8-12 facts) needs ~1k output tokens; the
    # default 256 truncated the JSON mid-object and the parse came back empty.
    llm = SarvamLLMClient(settings.sarvam_api_key, model="sarvam-105b",
                          max_tokens=1400)
    try:
        draft = await research_website(
            url=url, description=(body.get("description") or "").strip(),
            complete_json=llm.complete_json,
            openrouter_key=settings.openrouter_api_key)
        return draft
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    except Exception as e:  # noqa: BLE001
        logger.error("onboard.failed", error=str(e))
        return JSONResponse({"error": "research failed; try again"}, status_code=500)
    finally:
        await llm.close()


@app.post("/onboard/enhance")
async def onboard_enhance(request: Request):
    """One-liner behaviour description -> full production system prompt.
    Powers the '✨ Enhance' button in onboarding and the console editor —
    users bring ANY role ('debt recovery agent', 'order desk', 'survey
    caller') and get the hardened live-call prompt for it."""
    if _accounts_on():
        if await _auth.current_user(request) is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    desc = (body.get("description") or "").strip()
    if not desc:
        return JSONResponse({"error": "description required"}, status_code=400)
    from src.onboarding import enhance_prompt
    llm = SarvamLLMClient(settings.sarvam_api_key, model="sarvam-105b",
                          max_tokens=900)
    try:
        prompt = await enhance_prompt(
            description=desc, business_name=(body.get("business_name") or "").strip(),
            complete_json=llm.complete_json)
        return {"system_prompt": prompt}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    except Exception as e:  # noqa: BLE001
        logger.error("onboard.enhance_failed", error=str(e))
        return JSONResponse({"error": "enhancement failed; try again"},
                            status_code=500)
    finally:
        await llm.close()


def _chunk_doc_text(text: str, chunk_chars: int = 1800,
                    max_docs: int = 60) -> list[str]:
    """Split uploaded document text into standalone knowledge facts along
    paragraph boundaries (falls back to hard splits for wall-of-text files).
    Sized under the 4,000-char per-doc validation cap with headroom."""
    paras = [p.strip() for p in re.split(r"\n\s*\n|\r\n\s*\r\n", text) if p.strip()]
    docs: list[str] = []
    buf = ""
    for p in paras:
        while len(p) > chunk_chars:                 # oversized paragraph
            docs.append(p[:chunk_chars]); p = p[chunk_chars:]
        if len(buf) + len(p) + 1 > chunk_chars:
            if buf:
                docs.append(buf)
            buf = p
        else:
            buf = f"{buf}\n{p}" if buf else p
    if buf:
        docs.append(buf)
    return docs[:max_docs]


class ParseDocBody(BaseModel):
    filename: str = "document.txt"
    content_b64: str


@app.post("/onboard/parse-doc")
async def onboard_parse_doc(body: ParseDocBody, request: Request):
    """Uploaded knowledge file (.txt/.md/.pdf, base64) -> knowledge_docs
    chunks the agent editor can append. Same auth posture as research."""
    if _accounts_on():
        if await _auth.current_user(request) is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    if len(body.content_b64) > 7_000_000:           # ~5MB raw
        return JSONResponse({"error": "file too large (max 5MB)"}, status_code=413)
    try:
        raw = base64.b64decode(body.content_b64)
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "invalid file data"}, status_code=400)
    name = (body.filename or "").lower()
    if name.endswith(".pdf"):
        try:
            import io as _io
            from pypdf import PdfReader
            reader = PdfReader(_io.BytesIO(raw))
            text = "\n\n".join((page.extract_text() or "")
                               for page in reader.pages[:100])
        except Exception as e:  # noqa: BLE001
            logger.warning("parse_doc.pdf_failed", error=str(e))
            return JSONResponse({"error": "could not read that PDF"},
                                status_code=422)
    elif name.endswith((".txt", ".md", ".markdown", ".csv")):
        text = raw.decode("utf-8", errors="replace")
    else:
        return JSONResponse(
            {"error": "supported types: .txt, .md, .csv, .pdf"}, status_code=415)
    docs = _chunk_doc_text(text)
    if not docs:
        return JSONResponse({"error": "no readable text in that file"},
                            status_code=422)
    return {"docs": docs, "chars": sum(len(d) for d in docs)}


def _warm_agent_kb(agent: AgentConfig) -> None:
    """Fire-and-forget translation warm for one agent's docs (content-hash
    cached, so an unchanged doc costs nothing)."""
    if agent is not None and agent.knowledge_docs:
        asyncio.ensure_future(
            kb_i18n.warm(agent.knowledge_docs, api_key=settings.sarvam_api_key))


@app.post("/agents")
async def create_agent(request: Request):
    user, ws, err = await _console_ctx(request)
    if err is not None:
        return err
    body = await request.json()
    body.pop("workspace_id", None)             # server-controlled, never client
    if (verr := _validate_agent_body(body)) is not None:
        return JSONResponse({"error": verr}, status_code=400)
    # Cloned voices are provisioned ONLY by the voice-clone endpoint — a raw
    # inworld id here could be another tenant's clone driven through our key.
    if str(body.get("voice") or "").startswith("inworld:"):
        return JSONResponse(
            {"error": "cloned voices are set via the voice-clone endpoint"},
            status_code=400)
    agent = AgentConfig.from_dict(body)
    if ws is not None:
        # Accounts mode: agent belongs to the caller's workspace, and the id is
        # server-generated globally-unique (collisions suffixed, never 409).
        agent.workspace_id = ws
        agent.agent_id = _unique_agent_id(agent.agent_id or agent.name)
    if not agent.agent_id:
        return JSONResponse({"error": "agent_id required"}, status_code=400)
    try:
        await _agent_manager.create(agent)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    _warm_agent_kb(agent)
    return JSONResponse(agent.to_dict(), status_code=201)


@app.patch("/agents/{agent_id}")
async def update_agent(agent_id: str, request: Request):
    user, ws, err = await _console_ctx(request)
    if err is not None:
        return err
    existing = _agent_manager.get(agent_id)
    if existing is None or (ws is not None and existing.workspace_id != ws):
        return JSONResponse({"error": "not found"}, status_code=404)
    body = await request.json()
    if (verr := _validate_agent_body(body)) is not None:
        return JSONResponse({"error": verr}, status_code=400)
    # An inworld voice may only pass through unchanged (console re-saving the
    # agent). Setting a DIFFERENT clone id is the voice-clone endpoint's job —
    # otherwise any signed-in user could speak through any tenant's clone.
    v = str(body.get("voice") or "")
    if v.startswith("inworld:") and v != existing.voice:
        return JSONResponse(
            {"error": "cloned voices are set via the voice-clone endpoint"},
            status_code=400)
    # Switching a cloned agent back to a stock voice abandons the clone —
    # free its Inworld slot (best-effort) instead of leaking it.
    if (existing.voice.startswith("inworld:") and v and v != existing.voice
            and settings.inworld_api_key):
        asyncio.ensure_future(
            _inworld_delete_voice(existing.voice.split(":", 1)[1]))
    # id + owning workspace are immutable regardless of what the body says.
    merged = {**existing.to_dict(), **body,
              "agent_id": agent_id, "workspace_id": existing.workspace_id}
    await _agent_manager.update(AgentConfig.from_dict(merged))
    updated = _agent_manager.get(agent_id)
    _warm_agent_kb(updated)
    return updated.to_dict()


@app.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str, request: Request):
    user, ws, err = await _console_ctx(request)
    if err is not None:
        return err
    existing = _agent_manager.get(agent_id)
    if existing is None or (ws is not None and existing.workspace_id != ws):
        return JSONResponse({"error": "not found"}, status_code=404)
    if agent_id == _agent_store.default_id:
        return JSONResponse({"error": "cannot delete the default agent"}, status_code=400)
    ok = await _agent_manager.delete(agent_id)
    if not ok:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"deleted": agent_id}


def _context_text(context: dict | None) -> str:
    """Render per-call context vars into a system-prompt block."""
    if not context:
        return ""
    lines = "; ".join(f"{k}: {v}" for k, v in context.items())
    return f"Call context — {lines}"


class OutboundRequest(BaseModel):
    to: str
    agent_id: str | None = None
    from_number: str | None = None
    context: dict | None = None


class BatchCall(BaseModel):
    to: str
    context: dict | None = None


class BatchRequest(BaseModel):
    calls: list[BatchCall]
    agent_id: str | None = None
    from_number: str | None = None
    pace_per_min: float = 10.0


async def _place_outbound(*, to: str, agent_id: str | None,
                          from_number: str | None, context: dict | None) -> str:
    """Create one outbound call and pre-register its agent + context.
    Raises on failure; shared by /outbound and the batch dialer."""
    agent = _agent_store.resolve(agent_id=agent_id)
    # Caller-ID fallback chain: explicit -> configured -> the business's own
    # main number (natural for "the AI calls you" flows from the dashboard).
    from_num = (from_number or settings.telnyx_from_number
                or settings.main_agent_number)
    if not from_num:
        raise ValueError("no from_number / TELNYX_FROM_NUMBER")
    ccid = await _telnyx.create_call(
        to=to, from_=from_num, connection_id=settings.telnyx_connection_id)
    _call_registry.put(ccid, {"agent_id": agent.agent_id, "from": from_num,
                              "to": to, "context": context or {}},
                       now=time.monotonic())
    logger.info("outbound.initiated", to=to, agent=agent.agent_id, call=ccid)
    return ccid


@app.post("/outbound")
async def outbound(req: OutboundRequest, request: Request):
    """Initiate an outbound call for a business (campaigns, reminders, confirmations)."""
    if settings.outbound_api_key and request.headers.get("x-api-key") != settings.outbound_api_key:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if _outbound_bucket is not None and not _outbound_bucket.allow(time.monotonic()):
        logger.warning("outbound.rate_limited")
        return JSONResponse({"error": "rate limited"}, status_code=429)
    if not settings.telnyx_connection_id:
        return JSONResponse({"error": "TELNYX_CONNECTION_ID not configured"}, status_code=400)
    try:
        ccid = await _place_outbound(to=req.to, agent_id=req.agent_id,
                                     from_number=req.from_number, context=req.context)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:  # noqa: BLE001
        logger.error("outbound.failed", error=str(e))
        return JSONResponse({"error": "call initiation failed"}, status_code=502)
    return {"call_control_id": ccid}


@app.post("/outbound/batch")
async def outbound_batch(req: BatchRequest, request: Request):
    """Start a paced outbound campaign (reminders, follow-ups)."""
    if settings.outbound_api_key and request.headers.get("x-api-key") != settings.outbound_api_key:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not settings.telnyx_connection_id:
        return JSONResponse({"error": "TELNYX_CONNECTION_ID not configured"}, status_code=400)
    if not req.calls:
        return JSONResponse({"error": "calls list is empty"}, status_code=400)

    calls = [{"to": c.to, "context": c.context,
              "agent_id": req.agent_id, "from_number": req.from_number}
             for c in req.calls]
    batch = _batch_dialer.start(calls, pace_per_min=req.pace_per_min)
    return JSONResponse({"batch_id": batch.batch_id, "total": batch.total}, status_code=202)


@app.get("/outbound/batch/{batch_id}")
async def outbound_batch_status(batch_id: str, request: Request):
    if settings.outbound_api_key and request.headers.get("x-api-key") != settings.outbound_api_key:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    st = _batch_dialer.status(batch_id)
    if st is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return st.to_dict()


# ─── Landing-page "call me" demo ───
# The only endpoint where an anonymous click spends our money on a real
# carrier leg, so it is deny-by-default and rate-limited on three axes:
# per IP, per destination number, and platform-wide per hour. The prefix
# allowlist is the important one — unrestricted dialing is how toll fraud
# monetises a demo button (premium-rate / satellite ranges).

_demo_call_ip: dict[str, list[float]] = {}      # ip     -> call timestamps
_demo_call_num: dict[str, list[float]] = {}     # number -> call timestamps
_demo_call_all: list[float] = []                # platform-wide timestamps


def _prune(stamps: list[float], window_s: float, now: float) -> list[float]:
    return [t for t in stamps if now - t < window_s]


def _demo_call_gate(ip: str, number: str) -> str | None:
    """Returns a user-facing refusal, or None when the call may proceed."""
    now = time.monotonic()
    day, hour = 86400.0, 3600.0

    ips = _demo_call_ip[ip] = _prune(_demo_call_ip.get(ip, []), day, now)
    nums = _demo_call_num[number] = _prune(_demo_call_num.get(number, []), day, now)
    glob = _demo_call_all[:] = _prune(_demo_call_all, hour, now)

    if glob and len(glob) >= settings.demo_call_global_hourly:
        return "Our demo line is busy right now — please try again later."
    if ips and now - max(ips) < settings.demo_call_ip_cooldown_s:
        return "You just tried a demo call — please wait a few minutes."
    if len(ips) >= settings.demo_call_per_ip_daily:
        return "You've used your demo calls for today."
    if nums and now - max(nums) < settings.demo_call_number_cooldown_s:
        return "That number was just called — please wait a few minutes."
    if len(nums) >= settings.demo_call_per_number_daily:
        return "That number has used its demo calls for today."
    return None


def _demo_call_record(ip: str, number: str) -> None:
    now = time.monotonic()
    _demo_call_ip.setdefault(ip, []).append(now)
    _demo_call_num.setdefault(number, []).append(now)
    _demo_call_all.append(now)


class DemoCallBody(BaseModel):
    phone: str


@app.post("/demo/call-me")
async def demo_call_me(body: DemoCallBody, request: Request):
    """Public: the demo agent calls the visitor's phone. Heavily throttled."""
    if not (settings.demo_call_enabled and settings.telnyx_connection_id):
        return JSONResponse({"error": "Phone demo isn't available right now."},
                            status_code=503)
    number = "+" + re.sub(r"\D", "", body.phone or "")
    if not re.fullmatch(r"\+\d{8,15}", number):
        return JSONResponse(
            {"error": "Enter a valid number with country code, e.g. +91…"},
            status_code=400)
    allowed = [p.strip() for p in settings.demo_call_allowed_prefixes.split(",")
               if p.strip()]
    if not any(number.startswith(p) for p in allowed):
        return JSONResponse(
            {"error": f"Demo calls are available to {', '.join(allowed)} "
                      f"numbers for now."}, status_code=400)
    ip = request.client.host if request.client else "?"
    if (refusal := _demo_call_gate(ip, number)) is not None:
        return JSONResponse({"error": refusal}, status_code=429)

    demo_id = next(iter(_PUBLIC_DEMO_AGENTS), "") or "sonuslabs"
    try:
        # context rides into the call registry -> /media-stream reads the flag
        # and applies the demo time cap to this leg.
        ccid = await _place_outbound(to=number, agent_id=demo_id,
                                     from_number=None,
                                     context={"demo_call": True})
    except Exception as e:  # noqa: BLE001
        logger.error("demo_call.failed", error=str(e))
        return JSONResponse({"error": "Could not place the call — try again."},
                            status_code=502)
    # Only count calls we actually placed, so a carrier failure doesn't burn
    # the visitor's daily allowance.
    _demo_call_record(ip, number)
    logger.info("demo_call.placed", to=number[:6] + "…", call=ccid)
    return {"status": "calling", "max_seconds": settings.demo_call_max_seconds}


@app.get("/health")
async def health():
    return {"status": "ok", "active_sessions": _sessions.active, "agents": len(_agent_store)}


@app.get("/calls")
async def list_calls(request: Request, agent_id: str | None = None, limit: int = 50):
    """Recent call records for analytics/QA (most recent first)."""
    if _call_store is None:
        return {"calls": []}
    if _accounts_on():
        user, ws, err = await _console_ctx(request)
        if err is not None:
            return err
        records = await _call_store.recent(agent_id=agent_id, limit=min(limit, 500),
                                           workspace_id=ws)
    else:
        records = await _call_store.recent(agent_id=agent_id, limit=min(limit, 500))
    return {"calls": [r.to_row() for r in records]}


# ─── AI voice tester ───

import collections as _collections  # noqa: E402

_test_runs: dict[str, TestRun] = {}
_test_by_ccid: dict[str, str] = {}            # tester-leg ccid -> test_id
_test_agent_override: dict = {}               # {"agent_id": ...} while a test runs
_live_transcripts: _collections.deque = _collections.deque(maxlen=200)
SCENARIO_DIR = "scenarios"


def _test_event(ccid: str, msg: str) -> None:
    """Surface Telnyx call-lifecycle webhooks in the live test event feed."""
    tid = _test_by_ccid.get(ccid)
    if tid and (run := _test_runs.get(tid)):
        run._event(msg)


async def _record_by_call_id(call_id: str, after_ts: float = 0.0):
    """Find the test call's transcript: by call_id (loopback), else the newest
    record since the test began (PSTN), else synthesize from the in-process
    live-transcript buffer — which cannot race the DB save."""
    if _call_store is not None:
        recent = await _call_store.recent(limit=100)
        for r in recent:
            if r.call_id == call_id:
                return r
        for r in recent:                      # newest first
            if after_ts and r.started_at >= after_ts:
                return r
    lines = [x for x in _live_transcripts if x["t"] >= after_ts]
    if lines:
        from src.persistence.records import CallRecord, Turn
        return CallRecord(call_id=call_id, agent_id="live",
                          turns=[Turn(x["role"], x["text"], 0.0) for x in lines])
    return None


@app.get("/dashboard")
async def dashboard():
    from fastapi.responses import HTMLResponse
    from src.testing.dashboard import DASHBOARD_HTML
    return HTMLResponse(DASHBOARD_HTML)


@app.get("/test/scenarios")
async def test_scenarios():
    return {"scenarios": list_scenarios(SCENARIO_DIR)}


@app.get("/live-transcript")
async def live_transcript(request: Request, since: float = 0.0):
    """Live feed of user/assistant lines across active calls — powers the
    dashboard's real-time view for 'Call My Phone' conversations."""
    if _accounts_on():
        user, ws, err = await _console_ctx(request)
        if err is not None:
            return err
        mine = {a.agent_id for a in _ws_agents(ws)}
        return {"lines": [x for x in _live_transcripts
                          if x["t"] > since and x.get("agent_id") in mine],
                "active": _sessions.active}
    return {"lines": [x for x in _live_transcripts if x["t"] > since],
            "active": _sessions.active}


# ─── Phone numbers (pool model — see BUSINESS-PHONE-NUMBERS.md) ───

@app.get("/numbers")
async def list_numbers(request: Request):
    """My workspace's numbers + what's claimable from the pool."""
    user, ws, err = await _console_ctx(request)
    if err is not None:
        return err
    if ws is None:
        return JSONResponse({"error": "accounts mode required"}, status_code=404)
    from src.accounts.db import pool as _pool
    mine = await _pool().fetch(
        """SELECT number, country, monthly_paise, agent_id,
                  extract(epoch FROM assigned_at) AS assigned_at
           FROM phone_numbers WHERE workspace_id = $1::uuid ORDER BY assigned_at""",
        ws)
    avail = await _pool().fetch(
        """SELECT country, COUNT(*) AS n, MIN(monthly_paise) AS monthly_paise
           FROM phone_numbers WHERE status = 'available' GROUP BY country""")
    return {"numbers": [dict(r) for r in mine],
            "available": [dict(r) for r in avail]}


@app.post("/numbers/claim")
async def claim_number(request: Request):
    """Attach a pool number to one of my agents. Charges the first month's
    rent from the wallet up front; inbound routing is live immediately."""
    user, ws, err = await _console_ctx(request)
    if err is not None:
        return err
    if ws is None:
        return JSONResponse({"error": "accounts mode required"}, status_code=404)
    body = await request.json()
    agent_id = (body.get("agent_id") or "").strip()
    country = (body.get("country") or "US").strip().upper()
    agent = _agent_manager.get(agent_id)
    if agent is None or agent.workspace_id != ws:
        return JSONResponse({"error": "agent not found"}, status_code=404)
    from src.accounts.db import pool as _pool
    async with _pool().acquire() as con:
        async with con.transaction():
            # SKIP LOCKED: two simultaneous claims can't grab the same DID.
            row = await con.fetchrow(
                """SELECT number, monthly_paise FROM phone_numbers
                   WHERE status = 'available' AND country = $1
                   ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED""", country)
            if row is None:
                return JSONResponse(
                    {"error": f"no {country} numbers in stock right now — "
                              "we're adding more, check back soon"}, status_code=409)
            owner = await _billing.workspace_owner(ws)
            # con= : rent charge + DID assignment commit atomically (same tx).
            bal = await _billing.charge_fixed(
                owner, row["monthly_paise"], "number_rent",
                ref=f"number:{row['number']}:first-month", con=con)
            if bal is None:
                return JSONResponse(
                    {"error": "insufficient credits for the first month's rent — "
                              "add credits in Billing"}, status_code=400)
            await con.execute(
                """UPDATE phone_numbers SET status = 'assigned',
                   workspace_id = $2::uuid, agent_id = $3, assigned_at = now()
                   WHERE number = $1""", row["number"], ws, agent_id)
    # Attach the DID to the agent — by_phone routing is live from this moment.
    merged = agent.to_dict()
    if row["number"] not in merged["phone_numbers"]:
        merged["phone_numbers"] = [*merged["phone_numbers"], row["number"]]
    await _agent_manager.update(AgentConfig.from_dict(merged))
    logger.info("number.claimed", number=row["number"], agent=agent_id, ws=ws)
    return {"number": row["number"], "agent_id": agent_id,
            "monthly_paise": row["monthly_paise"]}


@app.post("/numbers/release")
async def release_number(request: Request):
    user, ws, err = await _console_ctx(request)
    if err is not None:
        return err
    if ws is None:
        return JSONResponse({"error": "accounts mode required"}, status_code=404)
    body = await request.json()
    number = (body.get("number") or "").strip()
    from src.accounts.db import pool as _pool
    row = await _pool().fetchrow(
        """UPDATE phone_numbers SET status = 'available', workspace_id = NULL,
           agent_id = NULL, assigned_at = NULL
           WHERE number = $1 AND workspace_id = $2::uuid
           RETURNING number""",
        number, ws)
    if row is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    # Detach from whichever workspace agent carries the DID (RETURNING sees the
    # post-update row, so scan the few workspace agents instead).
    for a in _ws_agents(ws):
        if number in a.phone_numbers:
            merged = a.to_dict()
            merged["phone_numbers"] = [n for n in merged["phone_numbers"] if n != number]
            await _agent_manager.update(AgentConfig.from_dict(merged))
    logger.info("number.released", number=number, ws=ws)
    return {"released": number}


@app.get("/admin/live")
async def admin_live(request: Request, since: float = 0.0):
    """Operator monitor: active session count + the raw live-caption feed
    across ALL tenants (in-process state, so it lives here not admin_routes)."""
    user = await _auth.current_user(request)
    if not _auth.is_admin(user):
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"active": _sessions.active,
            "lines": [x for x in _live_transcripts if x["t"] > since]}


@app.get("/agents-lite")
async def agents_lite(request: Request):
    """id+name list for the console (no secrets). Accounts mode: the caller's
    workspace agents. Legacy: everything minus _CONSOLE_HIDDEN_AGENTS."""
    if _accounts_on():
        user, ws, err = await _console_ctx(request)
        if err is not None:
            return err
        return {"agents": [{"agent_id": a.agent_id, "name": a.name or a.agent_id}
                           for a in _ws_agents(ws)]}
    return {"agents": [{"agent_id": a.agent_id, "name": a.name or a.agent_id}
                       for a in _agent_store.all()
                       if a.agent_id not in _CONSOLE_HIDDEN_AGENTS]}


class CallMeRequest(BaseModel):
    to: str
    agent_id: str | None = None


@app.post("/test/call-me")
async def call_me(req: CallMeRequest):
    """Dashboard: the selected agent calls YOUR phone so you can talk to it."""
    if not settings.telnyx_connection_id:
        return JSONResponse({"error": "TELNYX_CONNECTION_ID not configured"}, status_code=400)
    try:
        ccid = await _place_outbound(to=req.to, agent_id=req.agent_id,
                                     from_number=None,
                                     context={"note": "dashboard call-me test"})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:  # noqa: BLE001
        logger.error("call_me.failed", error=str(e))
        return JSONResponse({"error": "call initiation failed"}, status_code=502)
    return {"call_control_id": ccid}


@app.post("/test/start")
async def test_start(request: Request):
    body = await request.json() if await request.body() else {}
    name = (body or {}).get("scenario", "quick-latency")
    transport = (body or {}).get("transport", "loopback")
    path = f"{SCENARIO_DIR}/{name}.json"
    try:
        scenario = load_scenario(path)
    except FileNotFoundError:
        return JSONResponse({"error": f"unknown scenario '{name}'"}, status_code=404)

    place_call = hangup_call = None
    if transport == "pstn":
        if not (settings.tester_from_number and settings.main_agent_number
                and settings.telnyx_connection_id):
            return JSONResponse(
                {"error": "PSTN test needs TESTER_FROM_NUMBER, MAIN_AGENT_NUMBER "
                          "and TELNYX_CONNECTION_ID"}, status_code=400)

        async def place_call(test_id: str) -> str:
            ccid = await _telnyx.create_call(
                to=settings.main_agent_number,
                from_=settings.tester_from_number,
                connection_id=settings.telnyx_connection_id)
            _call_registry.put(ccid, {"mode": "tester", "test_id": test_id},
                               now=time.monotonic())
            _test_by_ccid[ccid] = test_id      # lifecycle webhooks -> event feed
            return ccid

        async def hangup_call(ccid: str) -> None:
            await _telnyx.hangup(ccid)

    port = request.url.port or settings.port
    run = TestRun(
        scenario,
        ws_url=f"ws://127.0.0.1:{port}/media-stream",
        api_key=settings.sarvam_api_key,
        get_record=_record_by_call_id,
        transport=transport,
        place_call=place_call,
        hangup_call=hangup_call,
    )
    _test_runs[run.test_id] = run
    if scenario.main_agent_id:
        _test_agent_override["agent_id"] = scenario.main_agent_id

    async def _run_and_clear():
        try:
            await run.run()
        finally:
            _test_agent_override.pop("agent_id", None)

    asyncio.create_task(_run_and_clear())
    logger.info("test.started", test_id=run.test_id, scenario=name, transport=transport)
    return {"test_id": run.test_id}


@app.websocket("/test/listen/{test_id}")
async def test_listen(websocket: WebSocket, test_id: str):
    """Live listen-in: streams the mixed conversation (PCM16 8k) to the browser."""
    await websocket.accept()
    run = _test_runs.get(test_id)
    if run is None:
        await websocket.close(code=4404)
        return
    q = run.core.subscribe()
    try:
        while True:
            data = await q.get()
            await websocket.send_bytes(data)
    except Exception:  # noqa: BLE001 — listener left
        pass
    finally:
        run.core.unsubscribe(q)


@app.get("/test/status/{test_id}")
async def test_status(test_id: str):
    run = _test_runs.get(test_id)
    if run is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    st = run.status()
    # Live feed for the dual-caller view: the main agent's transcript lines
    # since this test started, plus who is speaking right now.
    st["live_transcript"] = [x for x in _live_transcripts if x["t"] >= run.started_ts]
    st["tester_speaking"] = not run.core._speech.empty()
    st["agent_speaking"] = run.core.agent_speaking()
    return st


@app.get("/test/audio/{test_id}")
async def test_audio(test_id: str):
    from fastapi.responses import FileResponse
    run = _test_runs.get(test_id)
    if run is None or run.state not in ("done", "failed"):
        return JSONResponse({"error": "not ready"}, status_code=404)
    import os as _os
    if not _os.path.exists(run.wav_path):
        return JSONResponse({"error": "no audio"}, status_code=404)
    return FileResponse(run.wav_path, media_type="audio/wav",
                        filename=f"test-{test_id}.wav")


@app.get("/calls/search")
async def search_calls(request: Request, q: str, agent_id: str | None = None,
                       limit: int = 50):
    """Keyword search over call transcripts (QA: 'find calls mentioning refund')."""
    if _call_store is None:
        return {"calls": []}
    if _accounts_on():
        user, ws, err = await _console_ctx(request)
        if err is not None:
            return err
        records = await _call_store.search(q, agent_id=agent_id,
                                           limit=min(limit, 200), workspace_id=ws)
    else:
        records = await _call_store.search(q, agent_id=agent_id, limit=min(limit, 200))
    return {"calls": [r.to_row() for r in records]}


@app.get("/analytics")
async def analytics(request: Request, agent_id: str | None = None,
                    since: float | None = None):
    """Aggregate call stats (volume, avg duration, avg perceived latency,
    outcomes) for a business, optionally since a unix timestamp."""
    if _call_store is None:
        return {"total_calls": 0}
    if _accounts_on():
        user, ws, err = await _console_ctx(request)
        if err is not None:
            return err
        stats = await _call_store.stats(agent_id=agent_id, since=since,
                                        workspace_id=ws)
    else:
        stats = await _call_store.stats(agent_id=agent_id, since=since)
    return stats.to_dict()


# ─── Telnyx call-control webhook ───

@app.post("/webhook/telnyx")
async def telnyx_webhook(request: Request):
    raw = await request.body()
    if not verify_telnyx_signature(
        public_key_b64=settings.telnyx_public_key,
        signature_b64=request.headers.get("telnyx-signature-ed25519", ""),
        timestamp=request.headers.get("telnyx-timestamp", ""),
        payload=raw,
    ):
        logger.warning("telnyx.webhook.invalid_signature")
        return JSONResponse({"error": "invalid signature"}, status_code=401)

    body = json.loads(raw or b"{}")
    et = body.get("data", {}).get("event_type", "")
    p = body.get("data", {}).get("payload", {})
    ccid = p.get("call_control_id", "")

    if et == "call.initiated":
        # INBOUND only: route the dialed number to a business and answer.
        # (Outbound calls are pre-registered at /outbound; nothing to do here.)
        if p.get("direction", "incoming") == "incoming":
            to_number = p.get("to", "")
            # During a vertical test, the scenario decides which business
            # persona answers the main number (jeweller / dental / cafe...).
            override = _test_agent_override.get("agent_id")
            agent = (_agent_store.get(override) if override else None) \
                or _agent_store.resolve(to_number=to_number)
            # Prepaid gate: a workspace agent whose wallet can't cover ~5s of
            # talk doesn't answer at all (caller hears it ring out / disconnect
            # instead of an answered-then-dead call).
            if _accounts_on() and agent.workspace_id:
                if await _billing.balance_seconds_for_workspace(agent.workspace_id) < 5:
                    logger.warning("call.refused_no_credits", agent=agent.agent_id)
                    asyncio.ensure_future(_telnyx.hangup(ccid))
                    return JSONResponse({"status": "refused"})
            _call_registry.put(ccid, {"agent_id": agent.agent_id,
                                      "from": p.get("from", ""), "to": to_number},
                               now=time.monotonic())
            logger.info("call.routed", to=to_number, agent=agent.agent_id)
            if _test_by_ccid:                 # a test is live: narrate the far leg
                for tid in set(_test_by_ccid.values()):
                    if (run := _test_runs.get(tid)):
                        run._event(f"📞 main agent's number {to_number} is RINGING "
                                   f"(incoming from {p.get('from','')})")
            if settings.answer_delay_ms > 0:
                async def _answer_later(cc=ccid):
                    await asyncio.sleep(settings.answer_delay_ms / 1000)
                    await _telnyx.answer(cc)
                    if _test_by_ccid:
                        for tid in set(_test_by_ccid.values()):
                            if (run := _test_runs.get(tid)):
                                run._event("📞 main agent PICKED UP")
                asyncio.create_task(_answer_later())
            else:
                await _telnyx.answer(ccid)
    elif et == "call.ringing":
        _test_event(ccid, "📞 RINGING at the far end…")
    elif et == "call.answered":
        _test_event(ccid, "📞 answered — starting media")
        url = settings.public_url.replace("https://", "wss://") + "/media-stream"
        await _telnyx.streaming_start(ccid, stream_url=url)
    elif et == "call.hangup":
        _test_event(ccid, "📞 call hung up")
    return JSONResponse({"status": "ok"})


# ─── Voice lab: A/B female voices by ear ───

VOICE_LAB_CANDIDATES = ["ishita", "priya", "ritu", "neha", "kavya", "shreya",
                        "simran", "tanya"]


def _voice_sample_text(voice: str) -> str:
    """Per-voice self-intro — the voice says its OWN name. Generic SonusLabs
    branding, NO fixed company anywhere. Short so it renders + plays fast."""
    name = voice.capitalize()
    return f"Hi, I'm {name} — welcome to SonusLabs. I can speak eleven Indian languages."


# Rendered WAV bytes per voice, built once and reused so the preview button is
# instant (was ~1-2s of live TTS on every click).
_voice_sample_cache: dict[str, bytes] = {}


async def _render_voice_sample(voice: str) -> bytes:
    from src.testing.caller import render_utterances
    import io
    import wave as _wave
    text = _voice_sample_text(voice)
    audio = await render_utterances([text], "en-IN", voice, settings.sarvam_api_key)
    pcm = audio[text]
    buf = io.BytesIO()
    with _wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(pcm)
    return buf.getvalue()


async def _warm_voice_samples() -> None:
    """Pre-render every lab voice at startup, in PARALLEL, so all previews are
    warm within a few seconds (sequential took ~5s each = ~40s)."""
    async def _one(v: str) -> None:
        try:
            _voice_sample_cache[v] = await _render_voice_sample(v)
        except Exception as e:  # noqa: BLE001 — best-effort; endpoint re-renders on miss
            logger.warning("voice_sample.warm_failed", voice=v, error=str(e))
    await asyncio.gather(*(_one(v) for v in VOICE_LAB_CANDIDATES))
    logger.info("voice_samples.warmed", count=len(_voice_sample_cache))


@app.get("/voice-sample/{voice}")
async def voice_sample(voice: str):
    """Per-voice self-intro rendered in `voice`, served from a warm cache so the
    preview button is instant."""
    from fastapi.responses import Response
    if voice not in KNOWN_VOICES:
        return JSONResponse({"error": f"unknown voice '{voice}'"}, status_code=404)
    wav = _voice_sample_cache.get(voice)
    if wav is None:                       # cache miss (cold start): render + store
        wav = await _render_voice_sample(voice)
        _voice_sample_cache[voice] = wav
    return Response(content=wav, media_type="audio/wav",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/voice-lab")
async def voice_lab():
    return {"voices": VOICE_LAB_CANDIDATES}


# ─── Voice cloning (Inworld): "Clone your voice" on the demo widget ───
# English/Hindi only. Slots are scarce on Inworld's on-demand plan (5 custom
# voices), so every demo clone is short-lived by design: tracked in-process,
# expired by TTL, evicted oldest-first when the cap is hit, and orphans from a
# previous process swept via the "__demo-" voiceId marker. Only voice IDs this
# server itself created are accepted on /web-call — nobody can drive arbitrary
# voices through our key.

INWORLD_VOICES_URL = "https://api.inworld.ai/voices/v1/voices"
_demo_clones: dict[str, float] = {}          # voiceId -> monotonic created-at
_DEMO_CLONE_TTL_S = 30 * 60
_DEMO_CLONE_MAX = 1                          # persistent agent clones share the
                                             # 5-slot plan cap — demo gets ONE
_clone_last_by_ip: dict[str, float] = {}
_CLONE_IP_COOLDOWN_S = 45
_CLONE_LANGS = {"en": "EN_US", "hi": "HI_IN"}


def _inworld_headers() -> dict[str, str]:
    return {"Authorization": f"Basic {settings.inworld_api_key}"}


async def _inworld_delete_voice(voice_id: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.delete(f"{INWORLD_VOICES_URL}/{voice_id}",
                               headers=_inworld_headers())
        ok = r.status_code == 200
        logger.info("clone.deleted" if ok else "clone.delete_failed",
                    voice_id=voice_id, status=r.status_code)
        return ok
    except Exception as e:  # noqa: BLE001
        logger.warning("clone.delete_error", voice_id=voice_id, error=str(e))
        return False


async def _reap_demo_clones(make_room: bool = False) -> None:
    """Free clone slots: TTL-expired first, then orphans from earlier runs
    (identified by the '__demo-' marker in the voiceId), then — only if a new
    clone needs the room — the oldest live demo clone."""
    now = time.monotonic()
    for vid in [v for v, t in _demo_clones.items()
                if now - t > _DEMO_CLONE_TTL_S]:
        if await _inworld_delete_voice(vid):
            _demo_clones.pop(vid, None)
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get("https://api.inworld.ai/tts/v1/voices",
                            headers=_inworld_headers())
        for v in r.json().get("voices", []):
            vid = v.get("voiceId", "")
            if v.get("isCustom") and "__demo-" in vid and vid not in _demo_clones:
                await _inworld_delete_voice(vid)
    except Exception as e:  # noqa: BLE001
        logger.warning("clone.sweep_failed", error=str(e))
    while make_room and len(_demo_clones) >= _DEMO_CLONE_MAX:
        oldest = min(_demo_clones, key=_demo_clones.get)  # type: ignore[arg-type]
        await _inworld_delete_voice(oldest)
        _demo_clones.pop(oldest, None)


def _validate_clone_audio(audio_b64: str) -> str | None:
    """Shared sample checks for demo AND agent clones. None = acceptable."""
    if len(audio_b64) > 2_000_000:    # ~1.4MB raw ≈ 45s of 16k PCM16: way past need
        return "sample too large"
    try:
        raw = base64.b64decode(audio_b64)
    except Exception:  # noqa: BLE001
        return "invalid audio"
    if len(raw) < 100_000:                 # <~3s of 16k PCM16 — Inworld needs 3s+
        return "sample too short — record at least 4 seconds"
    return None


async def _inworld_clone(display_name: str, audio_b64: str,
                         lang_code: str = "EN_US",
                         tags: list[str] | None = None
                         ) -> tuple[str | None, str | None]:
    """Create an Inworld voice from a sample. → (voice_id, None) on success,
    (None, user-facing error) on failure — slot exhaustion gets its own
    message so the console can say WHY cloning stopped working."""
    payload = {
        "displayName": display_name,
        "langCode": lang_code,
        "voiceSamples": [{"audioData": audio_b64}],
        "tags": tags or [],
        "audioProcessingConfig": {"removeBackgroundNoise": True},
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(f"{INWORLD_VOICES_URL}:clone",
                             headers=_inworld_headers(), json=payload)
    except Exception as e:  # noqa: BLE001
        logger.error("clone.request_failed", error=str(e))
        return None, "cloning service unreachable"
    if r.status_code != 200:
        detail = r.text[:300]
        logger.error("clone.failed", status=r.status_code, detail=detail)
        if any(w in detail.lower() for w in ("limit", "quota", "maximum", "exceed")):
            return None, ("all voice slots are in use — remove an unused "
                          "cloned voice or upgrade the Inworld plan")
        return None, "cloning failed — try a longer, clearer recording"
    vid = (r.json().get("voice") or {}).get("voiceId", "")
    if not vid:
        return None, "cloning failed"
    return vid, None


class CloneVoiceBody(BaseModel):
    audio_b64: str                     # WAV (PCM16 mono 16k), base64
    language: str = "en"               # "en" | "hi"


@app.post("/voice-clone")
async def voice_clone(body: CloneVoiceBody, request: Request):
    if not settings.inworld_api_key:
        return JSONResponse({"error": "voice cloning is not configured"},
                            status_code=503)
    lang = _CLONE_LANGS.get(body.language)
    if lang is None:
        return JSONResponse({"error": "language must be 'en' or 'hi'"},
                            status_code=400)
    if (aerr := _validate_clone_audio(body.audio_b64)) is not None:
        code = 413 if aerr == "sample too large" else 400
        return JSONResponse({"error": aerr}, status_code=code)
    ip = request.client.host if request.client else "?"
    now = time.monotonic()
    if now - _clone_last_by_ip.get(ip, -1e9) < _CLONE_IP_COOLDOWN_S:
        return JSONResponse({"error": "please wait a moment between clones"},
                            status_code=429)
    _clone_last_by_ip[ip] = now
    await _reap_demo_clones(make_room=True)
    vid, cerr = await _inworld_clone(f"demo-{secrets.token_hex(4)}",
                                     body.audio_b64, lang_code=lang,
                                     tags=["demo-clone"])
    if vid is None:
        return JSONResponse({"error": cerr}, status_code=502)
    _demo_clones[vid] = time.monotonic()
    logger.info("clone.created", voice_id=vid, language=body.language)
    return {"voice_id": vid, "expires_in_s": _DEMO_CLONE_TTL_S}


@app.delete("/voice-clone/{voice_id}")
async def voice_clone_delete(voice_id: str):
    """Early cleanup (page close). Only demo-registry ids are deletable."""
    if voice_id not in _demo_clones:
        return JSONResponse({"error": "unknown clone"}, status_code=404)
    if await _inworld_delete_voice(voice_id):
        _demo_clones.pop(voice_id, None)
        return {"deleted": voice_id}
    return JSONResponse({"error": "delete failed"}, status_code=502)


# ─── Persistent clones: a workspace agent speaks in its owner's voice ───
# This is the ONLY path that may set an "inworld:" voice on an agent — the
# agent CRUD endpoints reject client-supplied inworld ids, so one tenant can
# never point an agent at another tenant's clone through our key.

async def _clone_agent_ctx(agent_id: str, request: Request):
    """Auth + ownership shared by the agent-clone endpoints."""
    user, ws, err = await _console_ctx(request)
    if err is not None:
        return None, err
    agent = _agent_manager.get(agent_id)
    if agent is None or (ws is not None and agent.workspace_id != ws):
        return None, JSONResponse({"error": "not found"}, status_code=404)
    return agent, None


@app.post("/agents/{agent_id}/voice-clone")
async def agent_voice_clone(agent_id: str, request: Request):
    if not settings.inworld_api_key:
        return JSONResponse({"error": "voice cloning is not configured"},
                            status_code=503)
    agent, err = await _clone_agent_ctx(agent_id, request)
    if err is not None:
        return err
    body = await request.json()
    audio_b64 = body.get("audio_b64") or ""
    if (aerr := _validate_clone_audio(audio_b64)) is not None:
        code = 413 if aerr == "sample too large" else 400
        return JSONResponse({"error": aerr}, status_code=code)
    slug = re.sub(r"[^a-z0-9-]", "", agent_id.lower())[:24]
    vid, cerr = await _inworld_clone(f"agent-{slug}-{secrets.token_hex(2)}",
                                     audio_b64, tags=["agent-clone", agent_id])
    if vid is None:
        return JSONResponse({"error": cerr}, status_code=502)
    old_voice = agent.voice
    merged = agent.to_dict()
    merged["voice"] = f"inworld:{vid}"
    await _agent_manager.update(AgentConfig.from_dict(merged))
    if old_voice.startswith("inworld:"):     # re-record: free the old slot
        await _inworld_delete_voice(old_voice.split(":", 1)[1])
    logger.info("clone.agent_created", agent=agent_id, voice_id=vid)
    return {"voice_id": vid, "voice": f"inworld:{vid}"}


@app.delete("/agents/{agent_id}/voice-clone")
async def agent_voice_clone_delete(agent_id: str, request: Request):
    agent, err = await _clone_agent_ctx(agent_id, request)
    if err is not None:
        return err
    if not agent.voice.startswith("inworld:"):
        return JSONResponse({"error": "agent has no cloned voice"},
                            status_code=404)
    vid = agent.voice.split(":", 1)[1]
    merged = agent.to_dict()
    merged["voice"] = "neha"                 # sensible stock default
    await _agent_manager.update(AgentConfig.from_dict(merged))
    await _inworld_delete_voice(vid)         # best-effort; agent already reverted
    logger.info("clone.agent_deleted", agent=agent_id, voice_id=vid)
    return {"voice": "neha", "deleted": vid}


# ─── Website widget: the drop-in embed script clients put on their site ───
@app.get("/embed.js")
async def embed_js():
    from fastapi.responses import FileResponse, Response
    from pathlib import Path as _P
    f = _P(__file__).resolve().parent.parent / "web" / "embed.js"
    if not f.is_file():
        return Response("// embed.js missing", media_type="application/javascript",
                        status_code=404)
    # Served to any origin (it's a public loader), cached briefly.
    return FileResponse(str(f), media_type="application/javascript",
                        headers={"Access-Control-Allow-Origin": "*",
                                 "Cache-Control": "public, max-age=300"})


async def _eleven_voice_usable(voice_id: str) -> bool:
    """Tiny synth probe — the DEFINITIVE usability check. Voice metadata can
    be visible while synthesis still 402s (library voices on free plans), so
    only an actual synthesis proves the voice works. Cost: one '.' char."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                headers={"xi-api-key": settings.elevenlabs_api_key},
                params={"output_format": "pcm_16000"},
                json={"text": ".", "model_id": settings.elevenlabs_tts_model})
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


# ─── Private demo page (client-specific; file lives OUTSIDE the repo) ───
@app.get("/demo")
async def private_demo():
    from fastapi.responses import FileResponse, HTMLResponse
    from pathlib import Path as _P
    # 404 unless .env points at a local file (private/ is untracked).
    if not settings.private_demo_file:
        return HTMLResponse("<p>not found</p>", status_code=404)
    f = _P(__file__).resolve().parent.parent / settings.private_demo_file
    if not f.is_file():
        return HTMLResponse("<p>demo file missing</p>", status_code=404)
    # no-store: a stale cached demo page mid-sales-pitch is worse than the
    # ~30KB re-download (bit us live: new UI invisible until a hard refresh).
    return FileResponse(str(f), media_type="text/html",
                        headers={"Cache-Control": "no-store"})


# ─── Non-verbal / emotion Phase-0 lab (listen + A/B; see scripts/nonverbal_lab.py) ───
from pathlib import Path as _NVPath  # noqa: E402
_NVLAB_DIR = _NVPath("data") / "nonverbal_lab"


@app.get("/lab/audio/{name}")
async def lab_audio(name: str):
    from fastapi.responses import FileResponse, Response
    if not name.replace("_", "").replace("-", "").isalnum():
        return Response(status_code=400)
    f = _NVLAB_DIR / f"{name}.wav"
    if not f.is_file():
        return Response(status_code=404)
    return FileResponse(str(f), media_type="audio/wav")


@app.get("/lab/nonverbal")
async def lab_nonverbal():
    from fastapi.responses import HTMLResponse
    import json as _json
    man = _NVLAB_DIR / "manifest.json"
    if not man.is_file():
        return HTMLResponse("<p style='font-family:system-ui;padding:24px'>No clips yet. Run: <code>python -m scripts.nonverbal_lab</code></p>")
    items = _json.loads(man.read_text(encoding="utf-8"))
    groups: dict[str, list] = {}
    for it in items:
        groups.setdefault(it["group"], []).append(it)
    rows = ""
    for g, its in groups.items():
        rows += f'<h2 style="font-family:Georgia,serif;margin:26px 0 10px;color:#E08A1E">{g}</h2>'
        for it in its:
            rows += ('<div style="display:flex;align-items:center;gap:14px;padding:8px 0;border-bottom:1px solid #2a251c;flex-wrap:wrap">'
                     f'<audio controls preload="none" src="/lab/audio/{it["name"]}" style="height:34px"></audio>'
                     f'<span style="color:#d8cfbe">{it["label"]}</span></div>')
    html = ('<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<body style="background:#17140f;color:#ede7db;font-family:system-ui;max-width:760px;margin:0 auto;padding:24px">'
            '<h1 style="font-family:Georgia,serif">Non-verbal voice lab · neha</h1>'
            '<p style="color:#8a806c">Phase 0: does neha make usable non-verbals from text, and do spliced clips sound seamless? '
            'Focus on <b>Group 5</b> — text-only vs spliced, same line.</p>' + rows + '</body>')
    return HTMLResponse(html)


# Per-language self-intro (the landing "Eleven languages" chips) — the assistant
# introduces itself IN that language, in the default voice. All 11 verified to
# render on bulbul:v3. Brand ("SonusLabs") stays Latin; the model reads mixed.
_LANG_INTRO = {
    "en-IN": "Hi! I am Neha, your SonusLabs assistant. I can speak eleven Indian languages.",
    "hi-IN": "नमस्ते! मैं नेहा हूँ, आपकी SonusLabs सहायक। मैं ग्यारह भारतीय भाषाएँ बोल सकती हूँ।",
    "kn-IN": "ನಮಸ್ಕಾರ! ನಾನು ನೇಹಾ, ನಿಮ್ಮ SonusLabs ಸಹಾಯಕಿ. ನಾನು ಹನ್ನೊಂದು ಭಾರತೀಯ ಭಾಷೆಗಳನ್ನು ಮಾತನಾಡಬಲ್ಲೆ.",
    "te-IN": "నమస్కారం! నేను నేహా, మీ SonusLabs సహాయకురాలిని. నేను పదకొండు భారతీయ భాషలు మాట్లాడగలను.",
    "ta-IN": "வணக்கம்! நான் நேஹா, உங்கள் SonusLabs உதவியாளர். நான் பதினொரு இந்திய மொழிகளில் பேச முடியும்.",
    "ml-IN": "നമസ്കാരം! ഞാൻ നേഹ, നിങ്ങളുടെ SonusLabs സഹായി. എനിക്ക് പതിനൊന്ന് ഇന്ത്യൻ ഭാഷകൾ സംസാരിക്കാൻ കഴിയും.",
    "mr-IN": "नमस्कार! मी नेहा, तुमची SonusLabs सहाय्यक. मी अकरा भारतीय भाषा बोलू शकते.",
    "bn-IN": "নমস্কার! আমি নেহা, আপনার SonusLabs সহকারী। আমি এগারোটি ভারতীয় ভাষায় কথা বলতে পারি।",
    "gu-IN": "નમસ્તે! હું નેહા છું, તમારી SonusLabs સહાયક. હું અગિયાર ભારતીય ભાષાઓ બોલી શકું છું.",
    "pa-IN": "ਸਤ ਸ੍ਰੀ ਅਕਾਲ! ਮੈਂ ਨੇਹਾ ਹਾਂ, ਤੁਹਾਡੀ SonusLabs ਸਹਾਇਕ। ਮੈਂ ਗਿਆਰਾਂ ਭਾਰਤੀ ਭਾਸ਼ਾਵਾਂ ਬੋਲ ਸਕਦੀ ਹਾਂ।",
    "od-IN": "ନମସ୍କାର! ମୁଁ ନେହା, ଆପଣଙ୍କ SonusLabs ସହାୟିକା। ମୁଁ ଏଗାର ଭାରତୀୟ ଭାଷାରେ କଥା ହୋଇପାରେ।",
}
_lang_sample_cache: dict[str, bytes] = {}


async def _render_lang_sample(lang: str) -> bytes:
    from src.testing.caller import render_utterances
    import io
    import wave as _wave
    text = _LANG_INTRO[lang]
    audio = await render_utterances([text], lang, settings.sarvam_tts_voice or "neha",
                                    settings.sarvam_api_key)
    pcm = audio[text]
    buf = io.BytesIO()
    with _wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(pcm)
    return buf.getvalue()


async def _warm_lang_samples() -> None:
    async def _one(lang: str) -> None:
        try:
            _lang_sample_cache[lang] = await _render_lang_sample(lang)
        except Exception as e:  # noqa: BLE001
            logger.warning("lang_sample.warm_failed", lang=lang, error=str(e))
    await asyncio.gather(*(_one(l) for l in _LANG_INTRO))
    logger.info("lang_samples.warmed", count=len(_lang_sample_cache))


@app.get("/language-sample/{lang}")
async def language_sample(lang: str):
    """The assistant's self-intro spoken IN `lang` (default voice), warm-cached."""
    from fastapi.responses import Response
    if lang not in _LANG_INTRO:
        return JSONResponse({"error": f"unsupported language '{lang}'"}, status_code=404)
    wav = _lang_sample_cache.get(lang)
    if wav is None:
        wav = await _render_lang_sample(lang)
        _lang_sample_cache[lang] = wav
    return Response(content=wav, media_type="audio/wav",
                    headers={"Cache-Control": "public, max-age=86400"})


# ─── Web call (browser mic <-> agent, no telephony) ───

@app.websocket("/web-call")
async def web_call(websocket: WebSocket):
    """Live call from the dashboard: browser sends raw PCM16 mono 16kHz binary
    frames; we send back PCM16 mono 8kHz binary audio + JSON text frames
    ({role, text}) for the live transcript. No carrier, no tunnel audio cost —
    and STT gets true 16kHz wideband instead of phone-narrowband."""
    await websocket.accept()
    if not _sessions.try_acquire():
        await websocket.close(code=1013)
        return

    stt = llm = tts = engine = engine_task = cap_task = None
    billed_ws = None                     # workspace to bill on teardown
    call_t0 = None
    try:
        agent = _agent_store.resolve(agent_id=websocket.query_params.get("agent_id"))
        demo_call = True                 # unauth/public call -> time cap applies
        credit_cap_s = 0
        if _accounts_on() and agent.agent_id not in _PUBLIC_DEMO_AGENTS:
            # THREE ways a private (workspace) agent can be reached:
            #  1. Website widget — agent.embed_enabled + the page Origin is in
            #     agent.embed_origins. NO secret key in the browser; the Origin
            #     header (browsers can't forge it on the WS handshake) is the gate.
            #  2. Session cookie (the owner in their own console).
            #  3. ?api_key= (programmatic / server-side).
            origin = websocket.headers.get("origin", "")
            embed_ok = (agent.embed_enabled and bool(agent.workspace_id)
                        and _origin_allowed(origin, agent.embed_origins))
            if not embed_ok:
                ws_user = await _auth.current_user(websocket)
                if ws_user is None:
                    qk = websocket.query_params.get("api_key", "")
                    if qk:
                        ws_user = await _apikeys.user_for_key(qk)
                allowed = (ws_user is not None and agent.workspace_id
                           and await _accounts_repo.is_member(agent.workspace_id,
                                                              ws_user["id"]))
                if not allowed:
                    logger.warning("webcall.denied", agent=agent.agent_id,
                                   origin=origin)
                    await websocket.close(code=1008)   # policy violation
                    return
            else:
                logger.info("webcall.embed", agent=agent.agent_id, origin=origin)
            demo_call = False            # owner/embed: no 3-min demo cap
            # Prepaid metering: the wallet must cover the call. Refuse at ~0,
            # and cap the call at what the balance can pay for — divided across
            # this workspace's other active calls (see _billed_active).
            concurrent = _billed_start(agent.workspace_id)
            billed_ws = agent.workspace_id
            credit_cap_s = (await _billing.balance_seconds_for_workspace(
                agent.workspace_id)) // concurrent
            if credit_cap_s < 5:
                await websocket.send_text(json.dumps(
                    {"type": "call_end", "reason": "no_credits"}))
                await websocket.close(code=1000)
                return
        logger.info("webcall.started", agent=agent.agent_id)
        warm_india_rates()               # rates hot before the caller asks

        stt = SarvamSTTClient(settings.sarvam_api_key, buffer_ms=100)
        await stt.connect(language=agent.language)
        llm = SarvamLLMClient(settings.sarvam_api_key,
                              model=agent.llm_model or settings.sarvam_llm_model)
        # Optional per-call voice + pace overrides (the console editor's talk
        # orb sends the LIVE, possibly-unsaved values so you hear exactly what
        # you see). Only sane values are honoured, else the agent's config wins.
        req_voice = websocket.query_params.get("voice")
        call_voice = req_voice if req_voice in VOICE_LAB_CANDIDATES else agent.voice
        call_pace = agent.voice_pace
        try:
            p = float(websocket.query_params.get("pace"))
            if 0.5 <= p <= 2.0:
                call_pace = p
        except (TypeError, ValueError):
            pass
        # Cloned voice routing — two server-verified sources only:
        #  1. a demo clone THIS process created (?voice=inworld:<id> must be
        #     in the registry), or the agent's own saved clone echoed back by
        #     the console editor (?voice == agent.voice);
        #  2. the agent's persistent clone from its config (set exclusively by
        #     the authed voice-clone endpoint).
        # An arbitrary inworld id in the URL can't ride on our Inworld key.
        inworld_vid = None
        if settings.inworld_api_key:
            if (req_voice or "").startswith("inworld:"):
                vid = req_voice.split(":", 1)[1]
                if vid in _demo_clones or req_voice == agent.voice:
                    inworld_vid = vid
            elif (agent.voice.startswith("inworld:")
                    and req_voice not in VOICE_LAB_CANDIDATES):
                inworld_vid = agent.voice.split(":", 1)[1]
        # ElevenLabs ("eleven:<voiceId>"): the private demo page's provider
        # dropdown. Demo-scoped: honoured only for public demo agents (or
        # legacy single-tenant mode) so tenants can't burn our EL quota with
        # arbitrary voices. Voice id sanitized before it touches the URL.
        eleven_vid = None
        if (settings.elevenlabs_api_key
                and (req_voice or "").startswith("eleven:")
                and (not _accounts_on()
                     or agent.agent_id in _PUBLIC_DEMO_AGENTS)):
            cand = req_voice.split(":", 1)[1] or settings.elevenlabs_voice_id
            if re.fullmatch(r"[A-Za-z0-9]{8,48}", cand):
                # Preflight the voice: a rejected voice (library voice on a
                # free plan) previously produced silent dead air — read live
                # as "the demo is broken". Tell the browser and fall back.
                if await _eleven_voice_usable(cand):
                    eleven_vid = cand
                else:
                    fb = settings.elevenlabs_voice_id
                    if cand != fb and await _eleven_voice_usable(fb):
                        eleven_vid = fb
                        await websocket.send_text(json.dumps({
                            "type": "tts_notice",
                            "message": ("That ElevenLabs voice isn't available "
                                        "on this plan (library voices need a "
                                        "paid ElevenLabs account) — using the "
                                        "default ElevenLabs voice instead.")}))
                    else:
                        await websocket.send_text(json.dumps({
                            "type": "tts_notice",
                            "message": ("ElevenLabs is unavailable right now — "
                                        "using the Sarvam voice instead.")}))
        if eleven_vid:
            tts = ElevenLabsTTSClient(settings.elevenlabs_api_key,
                                      model=settings.elevenlabs_tts_model,
                                      pace=call_pace)
            await tts.connect(language=agent.language, voice=eleven_vid,
                              sample_rate="16000")
            logger.info("webcall.elevenlabs_voice", voice_id=eleven_vid)
        elif inworld_vid:
            tts = InworldTTSClient(settings.inworld_api_key,
                                   model=settings.inworld_tts_model,
                                   pace=call_pace)
            await tts.connect(language=agent.language, voice=inworld_vid,
                              sample_rate="16000")
            logger.info("webcall.cloned_voice", voice_id=inworld_vid)
        else:
            tts = SarvamTTSClient(settings.sarvam_api_key, model=settings.sarvam_tts_model,
                                  pace=call_pace)
            # Wideband: bulbul synthesizes natively at 24k — the browser has no
            # telephony codec constraint, so it gets 16k PCM (2x the voice quality
            # of the 8k phone channel).
            await tts.connect(language=agent.language, voice=call_voice, sample_rate="16000")

        # The landing demo must introduce itself as the voice it ACTUALLY
        # speaks with: a stock voice greets by its own name, and a cloned
        # voice claims no name at all (heard live: a male caller's clone
        # introduced itself as "Neha"). replace() copies for this call only —
        # the store's agent is never mutated.
        if agent.agent_id == "sonuslabs":
            if inworld_vid:
                agent = dataclasses.replace(
                    agent,
                    greeting_text=("Hi! Yes — this is your own voice speaking. "
                                   "I'm your SonusLabs receptionist demo. "
                                   "Ask me anything, in any language!"),
                    system_prompt=agent.system_prompt.replace(
                        "You are Neha, the AI receptionist demo",
                        "You are an AI receptionist demo speaking in the "
                        "caller's own cloned voice — never call yourself Neha",
                    ).replace(
                        "(5) If asked who you are, reply with EXACTLY this "
                        "one sentence and stop: 'I'm Neha, the SonusLabs AI "
                        "receptionist demo.' ",
                        "(5) If asked who you are, reply with EXACTLY this "
                        "one sentence and stop: 'I'm your own voice, cloned "
                        "as a SonusLabs receptionist demo — I could answer "
                        "your business calls sounding just like this.' ",
                    ),
                )
            elif call_voice and call_voice != "neha":
                v = call_voice.capitalize()
                agent = dataclasses.replace(
                    agent,
                    greeting_text=agent.greeting_text.replace("Neha", v),
                    system_prompt=agent.system_prompt.replace("Neha", v),
                )

        # Coalesce 20ms frames into 80ms batches: 50 tiny buffers/sec made the
        # browser schedule a new AudioBufferSource every 20ms, and the
        # scheduling jitter + boundary count was audible as a steady
        # disturbance under speech. 12.5 msgs/sec = 4x fewer boundaries.
        _batch = bytearray()

        async def send_media(frame: bytes) -> None:
            _batch.extend(frame)                  # already PCM16 @16k
            if len(_batch) >= 640 * 2 * 4:        # 4 frames = 80ms
                out = bytes(_batch)
                _batch.clear()
                await websocket.send_bytes(out)

        def transcript_sink(role: str, text: str) -> None:
            logger.info("transcript", agent=agent.agent_id, role=role,
                        text=text[:200].encode("ascii", "replace").decode())
            payload = json.dumps({"role": role, "text": text})
            asyncio.ensure_future(websocket.send_text(payload))

        engine = build_engine(
            agent, stt=stt, llm=llm, tts=tts, send_media=send_media,
            on_transcript=transcript_sink,
            idle_reprompt_s=settings.idle_reprompt_ms / 1000,
            idle_hangup_s=settings.idle_hangup_ms / 1000,
            instant_pause=True,          # browser AEC keeps the mic echo-free
            sample_rate=16000, codec="pcm16",
        )
        engine_task = asyncio.create_task(engine.run())

        # Demo time cap (sonuslabs.ai): enforced HERE on the server so a user
        # cannot talk all day or bypass it by editing the client. Tell the
        # browser the limit up front so its countdown matches; a watchdog ends
        # the call at the cap with a distinguishable 'call_end' frame.
        # Demo calls: the marketing time cap. Billed calls: the balance-derived
        # cap — the same watchdog, so nobody talks past what the wallet covers.
        cap_s = settings.web_call_max_seconds if demo_call else credit_cap_s
        cap_reason = "time_limit" if demo_call else "credits_exhausted"
        call_t0 = time.monotonic()
        if cap_s and cap_s > 0:
            await websocket.send_text(json.dumps({"type": "call_start", "max_seconds": cap_s}))

            async def _time_cap() -> None:
                try:
                    await asyncio.sleep(cap_s)
                except asyncio.CancelledError:
                    return
                logger.info("webcall.time_limit", agent=agent.agent_id,
                            seconds=cap_s, reason=cap_reason)
                try:
                    await websocket.send_text(
                        json.dumps({"type": "call_end", "reason": cap_reason}))
                except Exception:  # noqa: BLE001
                    pass
                try:
                    await websocket.close(code=1000)
                except Exception:  # noqa: BLE001
                    pass

            cap_task = asyncio.create_task(_time_cap())

        async def handle_control(raw: str) -> None:
            """Mid-call control frames from the demo page. Today: set_voice —
            a SAME-provider switch applies in place (the clients support
            set_voice) and is heard on the agent's next reply."""
            try:
                m = json.loads(raw)
            except json.JSONDecodeError:
                return
            if m.get("type") != "set_voice":
                return
            want = str(m.get("voice") or "")
            if want.startswith("eleven:") and isinstance(tts, ElevenLabsTTSClient):
                vid = want.split(":", 1)[1]
                if not re.fullmatch(r"[A-Za-z0-9]{8,48}", vid):
                    return
                if await _eleven_voice_usable(vid):
                    await tts.set_voice(vid)
                    logger.info("webcall.voice_switch", voice_id=vid)
                    await websocket.send_text(json.dumps(
                        {"type": "voice_changed", "voice": vid}))
                else:
                    await websocket.send_text(json.dumps({
                        "type": "tts_notice",
                        "message": ("That voice isn't available on this "
                                    "ElevenLabs plan — keeping the current "
                                    "voice.")}))
            elif want in VOICE_LAB_CANDIDATES and isinstance(tts, SarvamTTSClient):
                await tts.set_voice(want)
                logger.info("webcall.voice_switch", voice=want)
                await websocket.send_text(json.dumps(
                    {"type": "voice_changed", "voice": want}))

        # Reader: browser PCM16-16k -> local VAD (20ms frames) + STT.
        FRAME16 = 640                    # 20ms of PCM16 @16k
        buf = b""
        loud_run = quiet_run = 0
        vad_active = False
        THRESH = 500                     # browser-AEC'd mic: fixed floor works
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if not data:
                if msg.get("text"):
                    await handle_control(msg["text"])
                continue
            buf += data
            while len(buf) >= FRAME16:
                frame, buf = buf[:FRAME16], buf[FRAME16:]
                if audioop.rms(frame, 2) > THRESH:
                    loud_run += 1
                    quiet_run = 0
                else:
                    quiet_run += 1
                    loud_run = 0
                if not vad_active and loud_run >= 2:
                    vad_active = True
                    stt.inject_vad(True)
                elif vad_active and quiet_run == 20:
                    asyncio.ensure_future(stt.flush())   # early endpoint hint
                elif vad_active and quiet_run >= 30:
                    vad_active = False
                    stt.inject_vad(False)
            await stt.send_audio(data)
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        logger.error("webcall.error", error=str(e))
    finally:
        # Bill the talk time, not our teardown time: snapshot the clock BEFORE
        # engine/client closes (those awaits can add seconds).
        call_seconds = (time.monotonic() - call_t0) if call_t0 is not None else 0.0
        if cap_task is not None:
            cap_task.cancel()
            try:
                await cap_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if engine_task is not None:
            engine_task.cancel()
            try:
                await engine_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for client in (stt, tts, llm):
            if client is not None:
                try:
                    await client.close()
                except Exception:  # noqa: BLE001
                    pass
        if billed_ws:
            _billed_end(billed_ws)
            if call_seconds > 0:
                # Bill the seconds actually used (never raises — billing.py).
                await _billing.charge_workspace_usage(
                    billed_ws, call_seconds,
                    ref=f"web:{agent.agent_id}:{int(time.time())}")
        _sessions.release()
        logger.info("webcall.ended")


# ─── Media stream ───

@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    await websocket.accept()

    if not _sessions.try_acquire():
        logger.warning("session.rejected_over_capacity", active=_sessions.active)
        await websocket.close(code=1013)   # 1013 = try again later
        return

    stt = llm = tts = recorder = agent = warmup_task = engine = None
    credit_cap_task = demo_cap_task = None
    billed_phone_ws = None
    resampler = Resampler()
    try:
        # Wait for the stream-start frame; it carries the call id.
        start_frame = {}
        while True:
            msg = json.loads(await websocket.receive_text())
            if msg.get("event") == "start":
                start_frame = msg
                break
        ccid = start_frame.get("start", {}).get("call_control_id", "")
        stream_id = (start_frame.get("stream_id")
                     or start_frame.get("start", {}).get("stream_id", ""))
        media_encoding = start_frame.get("start", {}).get(
            "media_format", {}).get("encoding", "")

        # Resolve which business this call belongs to (webhook stashed it by id).
        info = (_call_registry.pop(ccid, now=time.monotonic()) if ccid else None) or {}

        # AI voice tester leg: this media stream belongs to the TESTER side of a
        # real test call — hand it to the runner instead of building an engine.
        if info.get("mode") == "tester":
            run = _test_runs.get(info.get("test_id", ""))
            if run is not None and run.state not in ("done", "failed"):
                # Re-register immediately: if this WS drops and Telnyx
                # reconnects the stream, the new socket must route back HERE —
                # a consumed entry once sent a reconnected tester leg to the
                # DEFAULT agent, and two AIs talked nonsense to each other.
                _call_registry.put(ccid, info, now=time.monotonic())
                logger.info("tester.leg_attached", test_id=run.test_id)
                await run.attach_pstn(websocket, media_encoding or "PCMU")
            else:
                logger.warning("tester.leg_rejected", reason="run not active")
            _sessions.release()
            logger.info("tester.leg_closed")
            return

        # Loopback test legs never pass through the webhook, so the scenario's
        # agent override must ALSO apply here — otherwise loopback tests run
        # against the default persona (found: stale answers that no PSTN test
        # could reproduce).
        agent_id = info.get("agent_id") or _test_agent_override.get("agent_id")
        agent = (_agent_store.get(agent_id) if agent_id else None) or _agent_store.default()
        warm_india_rates()               # rates hot before the caller asks
        logger.info("call.started", agent=agent.agent_id,
                    media_format=str(start_frame.get("start", {}).get("media_format", {})),
                    decoding="PCMA" if "PCMA" in (media_encoding or "PCMA").upper() else "PCMU")

        recorder = CallRecorder(
            call_id=ccid, agent_id=agent.agent_id,
            from_number=info.get("from", ""), to_number=info.get("to", ""),
            started_at=time.time(), clock=time.monotonic,
        )
        if agent.workspace_id:
            # Tenant tag: the Postgres store lifts this into an indexed column
            # so /calls and /analytics can filter per workspace.
            recorder.record.metadata["workspace_id"] = agent.workspace_id
        if _accounts_on() and agent.workspace_id:
            # Prepaid cap: end the phone call when the wallet runs out (same
            # principle as the web-call watchdog — enforcement, not trust).
            # Budget divides across the workspace's other active billed calls.
            billed_phone_ws = agent.workspace_id
            _n = _billed_start(billed_phone_ws)
            credit_s = (await _billing.balance_seconds_for_workspace(
                agent.workspace_id)) // _n

            async def _credit_cap(cc=ccid, limit=max(5, credit_s)) -> None:
                try:
                    await asyncio.sleep(limit)
                except asyncio.CancelledError:
                    return
                logger.info("call.credits_exhausted", agent=agent.agent_id,
                            seconds=limit)
                try:
                    await _telnyx.hangup(cc)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    await websocket.close(code=1000)
                except Exception:  # noqa: BLE001
                    pass

            credit_cap_task = asyncio.create_task(_credit_cap())

        eag = agent_eagerness(agent)
        turn_bucket = (
            TokenBucket(agent.max_turns_per_min, agent.max_turns_per_min / 60.0)
            if agent.max_turns_per_min > 0 else None
        )

        stt = SarvamSTTClient(
            settings.sarvam_api_key,
            buffer_ms=settings.stt_buffer_ms,
            high_vad_sensitivity=eag.high_vad_sensitivity,
        )
        llm = SarvamLLMClient(
            settings.sarvam_api_key,
            base_url=settings.sarvam_llm_base_url,
            model=agent.llm_model or settings.sarvam_llm_model,
            reasoning_effort=(agent.llm_reasoning_effort or None)
                             if agent.llm_reasoning_effort
                             else settings.sarvam_llm_reasoning_effort,
        )
        # Phone calls speak the agent's persistent clone too — same 8kHz PCM16
        # contract as Sarvam, so the engine's mulaw framing is untouched.
        phone_vid = (agent.voice.split(":", 1)[1]
                     if agent.voice.startswith("inworld:")
                     and settings.inworld_api_key else None)
        if phone_vid:
            tts = InworldTTSClient(settings.inworld_api_key,
                                   model=settings.inworld_tts_model,
                                   pace=agent.voice_pace)
        else:
            tts = SarvamTTSClient(settings.sarvam_api_key, model=settings.sarvam_tts_model,
                                  pace=agent.voice_pace)

        dsp = None
        if (agent.enable_input_dsp or agent.enable_echo_cancellation
                or agent.enable_noise_suppression):
            dsp = InboundDSP(
                enable_gate=agent.enable_input_dsp,
                enable_agc=agent.enable_input_dsp,
                enable_echo=agent.enable_echo_cancellation,
                enable_denoise=agent.enable_noise_suppression,
            )

        filler = None
        if agent.enable_fillers:
            filler = FillerPlayer(agent.language)
            filler.load()

        # The peer number to show in Live/Logs: for outbound it's who we dialed
        # (info["to"]); for inbound it's the caller (info["from"]).
        _peer = info.get("to") or info.get("from") or ""

        def transcript_sink(role: str, text: str) -> None:
            logger.info("transcript", agent=agent.agent_id, role=role,
                        text=text[:200].encode("ascii", "replace").decode())
            if recorder is not None:
                recorder.transcript(role, text)
            _live_transcripts.append(
                {"t": time.time(), "call_id": ccid, "agent_id": agent.agent_id,
                 "number": _peer, "role": role, "text": text})

        def metrics_sink(breakdown: dict) -> None:
            if recorder is not None:
                recorder.metric(breakdown)

        # Per-call echo profile: learns the line's echo delay+gain from the
        # greeting, then predicts how loud the agent's own echo should be on
        # each inbound frame so the VAD only fires on genuine caller speech.
        # (Static thresholds failed: echo blocked barge-in ~1.5s on real calls.)
        echo_profile = EchoProfile()

        async def send_media(frame: bytes) -> None:
            pcm = audioop.ulaw2lin(frame, 2)
            echo_profile.feed_out(float(audioop.rms(pcm, 2)))   # race-free feed
            # Tap outbound audio as the echo-canceller reference.
            if dsp is not None and dsp.echo is not None:
                dsp.feed_reference(pcm)
            await websocket.send_text(json.dumps({
                "event": "media",
                "media": {"payload": base64.b64encode(frame).decode("ascii")},
            }))

        # Connect STT and TTS in PARALLEL — serially this added ~0.5-1s to the
        # time-to-greeting (measured by the synthetic caller).
        await asyncio.gather(
            stt.connect(language=agent.language),
            tts.connect(language=agent.language,
                        voice=phone_vid or agent.voice, sample_rate="8000"),
        )

        # Pre-rendered greeting: instant "hello" instead of ~3.5s cold TTS
        # (REST-rendered, disk-cached per text+language+voice). Cloned voices
        # skip it — the renderer is Sarvam-only; the engine speaks the
        # greeting live through Inworld instead.
        greeting_audio = None
        if agent.greeting_text and not phone_vid:
            try:
                from src.testing.caller import render_utterances
                rendered = await render_utterances(
                    [agent.greeting_text], agent.language, agent.voice,
                    settings.sarvam_api_key)
                greeting_audio = rendered.get(agent.greeting_text)
            except Exception as e:  # noqa: BLE001 — fall back to live TTS
                logger.warning("greeting.prerender_failed", error=str(e))

        def clear_carrier_buffer() -> None:
            # Telnyx media-stream 'clear': drops audio already buffered on
            # their side so a barge-in silences the caller's ear ~instantly
            # instead of draining 1-1.6s of queued speech. The stream_id is
            # required for the carrier to address the right stream.
            msg = {"event": "clear"}
            if stream_id:
                msg["stream_id"] = stream_id
            asyncio.ensure_future(websocket.send_text(json.dumps(msg)))

        engine = build_engine(
            agent,
            stt=stt, llm=llm, tts=tts, send_media=send_media,
            filler=filler, on_transcript=transcript_sink, on_metrics=metrics_sink,
            on_false_recovery=echo_profile.bump_gain,
            on_pause=clear_carrier_buffer,
            turn_bucket=turn_bucket,
            idle_reprompt_s=settings.idle_reprompt_ms / 1000,
            idle_hangup_s=settings.idle_hangup_ms / 1000,
            extra_context=_context_text(info.get("context")),
            greeting_audio=greeting_audio,
            instant_pause=settings.bargein_instant_pause,
        )
        if os.environ.get("PUMP_TAP"):
            engine.tap = bytearray()   # capture exactly what we send (debug)

        # Human handoff: give the LLM a transfer_call tool bound to this call.
        if agent.transfer_numbers and ccid:
            attach_transfer_tool(
                engine, agent,
                transfer_action=lambda number, _ccid=ccid: _telnyx.transfer(_ccid, to=number),
            )

        # Warm the LLM connection while the greeting plays so the first user
        # turn doesn't pay client construction + TLS handshake.
        warmup_task = asyncio.create_task(llm.warmup())

        async def audio_reader():
            """Telnyx media -> PCM16 16kHz -> STT, with LOCAL barge-in VAD.

            Sarvam's live WS sends no VAD signals, so the engine's instant-pause
            path never fired (live barge-in waited ~2s for a transcript). A
            simple energy detector here injects speech-start/end events: three
            consecutive loud frames (60ms) while the agent is speaking fires the
            pause; ~240ms of quiet fires the end signal for fast recovery."""
            loud_run = quiet_run = 0
            vad_active = False
            while True:
                try:
                    d = json.loads(await websocket.receive_text())
                except WebSocketDisconnect:
                    return
                except Exception:
                    continue
                ev = d.get("event")
                if ev == "media":
                    b64 = d.get("media", {}).get("payload", "")
                    if b64:
                        # Decode per the leg's actual media_format (PCMA for
                        # Indian routes, PCMU for US) — never assume.
                        raw = base64.b64decode(b64)
                        if "PCMA" in (media_encoding or "PCMA").upper():
                            pcm8 = audioop.alaw2lin(raw, 2)
                        else:
                            pcm8 = audioop.ulaw2lin(raw, 2)
                        if dsp is not None:
                            pcm8 = dsp.process(pcm8)   # echo-cancel -> gate -> AGC

                        # Local barge-in VAD with the learned echo model: the
                        # caller must clearly exceed the PREDICTED echo of the
                        # agent's own voice for this exact frame.
                        in_rms = float(audioop.rms(pcm8, 2))
                        echo_profile.observe_in(in_rms)
                        # Speech = louder than predicted echo AND not tracking
                        # the outbound envelope (echo correlates ~1; callers don't).
                        if (in_rms > echo_profile.speech_threshold()
                                and echo_profile.echo_correlation() < 0.65):
                            loud_run += 1
                            quiet_run = 0
                        else:
                            quiet_run += 1
                            loud_run = 0
                        if not vad_active and loud_run >= 2:
                            # 2 frames (40ms) to confirm: shaves 20ms off every
                            # barge-in; the guard stack absorbs rare blips.
                            vad_active = True
                            stt.inject_vad(True)
                        elif vad_active and quiet_run == 20:
                            # Early endpoint hint at 400ms of quiet: force the
                            # STT segment to finalize NOW (final lands ~350-450ms
                            # later) WITHOUT firing VAD END — barge-in/resume
                            # semantics keep the safer 600ms window below. If
                            # this was only a mid-sentence breath, the fragment
                            # is held and merged by smart endpointing
                            # (looks_continuable), so nothing is answered early.
                            asyncio.ensure_future(stt.flush())
                        elif vad_active and quiet_run >= 30:
                            # 600ms of quiet = the caller is really done. Humans
                            # breathe 300-500ms BETWEEN clauses mid-sentence; a
                            # 240ms window fired END in that gap, the agent
                            # resumed over the caller, and barge-in degraded to
                            # a ~1.2s stutter (heard on 7 consecutive calls).
                            vad_active = False
                            stt.inject_vad(False)

                        await stt.send_audio(resampler.up_8k_to_16k(pcm8))
                elif ev == "stop":
                    return

        # Landing-page demo leg: hard time cap, with the agent announcing the
        # ending first so the caller isn't cut off mid-sentence by a silent
        # hangup. Enforced HERE (server side) — the visitor has no client to
        # trust, and every extra second is carrier spend.
        if (info.get("context") or {}).get("demo_call"):
            async def _demo_cap(cc=ccid) -> None:
                warn = max(0, settings.demo_call_max_seconds
                           - settings.demo_call_warn_seconds)
                try:
                    await asyncio.sleep(warn)
                    await engine.announce(
                        f"By the way, this demo call ends in about "
                        f"{settings.demo_call_warn_seconds} seconds.")
                    await asyncio.sleep(settings.demo_call_warn_seconds)
                except asyncio.CancelledError:
                    return
                logger.info("demo_call.time_limit",
                            seconds=settings.demo_call_max_seconds)
                try:
                    await _telnyx.hangup(cc)
                except Exception:  # noqa: BLE001
                    pass

            demo_cap_task = asyncio.create_task(_demo_cap())

        reader = asyncio.create_task(audio_reader())
        engine_task = asyncio.create_task(engine.run())
        done, pending = await asyncio.wait(
            {reader, engine_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    except Exception as e:
        logger.error("media_stream.error", error=str(e))
    finally:
        # Finalize + (optionally) summarize the call BEFORE closing the LLM,
        # then persist and notify the business webhook.
        if recorder is not None:
            try:
                record = recorder.finalize(ended_at=time.time())
                if settings.enable_call_summary and llm is not None and record.turns:
                    try:
                        summary = await summarize_call(llm.complete_json, record.turns)
                        record.metadata.update(summary)
                        if summary.get("outcome"):
                            record.outcome = summary["outcome"]
                    except Exception as e:  # noqa: BLE001
                        logger.warning("call_summary_failed", error=str(e))
                if _call_store is not None:
                    await _call_store.save(record)
                if agent is not None and agent.webhook_url:
                    await post_event(agent.webhook_url,
                                     {"event": "call.completed", **record.to_row()},
                                     secret=agent.webhook_secret)
                # Prepaid metering: bill the phone call's seconds to the
                # owning workspace's wallet (accounts mode; platform/demo
                # agents have no workspace and are not billed).
                if (_accounts_on() and agent is not None and agent.workspace_id
                        and record.duration_s):
                    await _billing.charge_workspace_usage(
                        agent.workspace_id, record.duration_s,
                        ref=f"call:{record.call_id or 'unknown'}")
            except Exception as e:  # noqa: BLE001 — teardown must never crash
                logger.error("call_teardown_failed", error=str(e))
        if engine is not None and engine.tap:
            try:                       # what WE sent, as audio (debug tap)
                import wave as _wave
                os.makedirs("data/test_runs", exist_ok=True)
                safe = re.sub(r"\W", "", (ccid or "x"))[-12:]  # ccid has ':' — illegal on Windows
                with _wave.open(f"data/test_runs/sent-{safe}.wav", "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
                    w.writeframes(audioop.ulaw2lin(bytes(engine.tap), 2))
                logger.info("pump.tap_saved", bytes=len(engine.tap))
            except Exception as e:  # noqa: BLE001
                logger.warning("pump.tap_save_failed", error=str(e))
        if billed_phone_ws:
            _billed_end(billed_phone_ws)
        if credit_cap_task is not None:
            credit_cap_task.cancel()
        if demo_cap_task is not None:
            demo_cap_task.cancel()
        if warmup_task is not None:
            warmup_task.cancel()
        if stt is not None:
            await stt.close()
        if tts is not None:
            await tts.close()
        if llm is not None:
            await llm.close()
        _sessions.release()
        logger.info("call.cleanup")


# ─── Serve the SonusLabs frontend (single origin: one tunnel serves site + API) ───
# Registered LAST so every API/WS route above takes precedence; the catch-all
# only handles SPA paths (/, /create, /console) and static assets.
from pathlib import Path as _Path                                   # noqa: E402
from fastapi.staticfiles import StaticFiles                          # noqa: E402
from fastapi.responses import FileResponse as _FileResponse          # noqa: E402

_DIST = _Path(__file__).resolve().parent.parent / "sonuslabs-web" / "dist"
if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def _spa(full_path: str):
        candidate = _DIST / full_path
        if full_path and candidate.is_file():          # real file (favicon, etc.)
            return _FileResponse(str(candidate))
        return _FileResponse(str(_DIST / "index.html"))  # SPA shell for client routes
    logger.info("frontend.mounted", dist=str(_DIST))
