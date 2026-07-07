"""Google sign-in + session cookie + the /auth and /workspaces routes.

Flow (authorization-code, hand-rolled on httpx — no heavyweight OAuth lib):
  GET /auth/google/login?next=/console
      -> 302 to Google consent. A signed state (random nonce + validated next)
         travels BOTH as the OAuth state param and in a short-lived cookie, so
         the callback can verify the round-trip actually started in this browser.
  GET /auth/google/callback?code&state
      -> verify state, exchange code, fetch userinfo, upsert user, ensure a
         default workspace, mint a server-side session (revocable; only the
         token hash is stored), set the HttpOnly cookie, redirect to next.

Accounts mode is active only when DATABASE_URL is set. DEV_LOGIN_ENABLED adds
GET /auth/dev-login (local testing before Google credentials exist) — it must
NEVER be on in production.
"""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

import httpx
import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer

from src.accounts import repo
from src.accounts.db import connected
from src.config import settings
from src.util.ratelimit import TokenBucket

# Blunt DoS brake on the login endpoints (they mint sessions + hit Google).
# Process-global, 30 burst / ~30 per minute sustained. Per-IP limiting belongs
# at the edge (Caddy/Cloudflare) — this is the backstop behind it.
_login_bucket = TokenBucket(30, 0.5)

logger = structlog.get_logger()
router = APIRouter()

SESSION_COOKIE = "sl_session"
_STATE_COOKIE = "sl_oauth_state"
_STATE_MAX_AGE = 600  # seconds to complete the Google round-trip

_GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"


def accounts_enabled() -> bool:
    return bool(settings.database_url)


def telephony_enabled() -> bool:
    """Is real phone calling wired up? (Telnyx configured + a public webhook
    URL to receive events.) False on the PAYG launch where prod has no Telnyx —
    the console then shows the calling features as 'coming soon'."""
    return bool(settings.telnyx_connection_id and settings.telnyx_api_key
                and settings.public_url)


def _signer() -> URLSafeTimedSerializer:
    if not settings.session_secret:
        raise RuntimeError("SESSION_SECRET required in accounts mode")
    return URLSafeTimedSerializer(settings.session_secret, salt="oauth-state")


def _allowed_origins() -> list[str]:
    csv = settings.cors_allow_origins or ""
    origins = [o.strip().rstrip("/") for o in csv.split(",") if o.strip()]
    if settings.public_url:
        origins.append(settings.public_url.rstrip("/"))
    return origins


def _safe_next(nxt: str | None) -> str:
    """Open-redirect guard: relative paths only, or absolute URLs whose origin
    is explicitly ours (dev: the Vite origin; prod: the public URL)."""
    nxt = (nxt or "").strip()
    if not nxt:
        return "/console"
    if nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    for origin in _allowed_origins():
        if nxt == origin or nxt.startswith(origin + "/"):
            return nxt
    return "/console"


def _base_url(request: Request) -> str:
    """Base URL for the OAuth redirect_uri — must match a URI registered in
    Google AND be browser-reachable.

    Precedence: OAUTH_REDIRECT_BASE (explicit) > PUBLIC_URL > request host.
    OAUTH_REDIRECT_BASE exists because PUBLIC_URL can be pointed at a telephony
    tunnel (Telnyx media) that is NOT a registered Google redirect — in that
    case set OAUTH_REDIRECT_BASE=http://localhost:8001 so sign-in still works.
    In prod both are the same (sonuslabs.online), so leaving it unset is fine."""
    if settings.oauth_redirect_base:
        return settings.oauth_redirect_base.rstrip("/")
    if settings.public_url:
        return settings.public_url.rstrip("/")
    return f"{request.url.scheme}://{request.url.netloc}"


def _cookie_secure(request: Request) -> bool:
    return _base_url(request).startswith("https")


def _set_session_cookie(resp, token: str, request: Request) -> None:
    resp.set_cookie(
        SESSION_COOKIE, token, max_age=repo.SESSION_TTL_DAYS * 86400,
        httponly=True, samesite="lax", secure=_cookie_secure(request), path="/")


# ─── helpers used by main.py for gating ───

async def current_user(request) -> dict | None:
    """Resolve the caller to a user: session cookie (browser) OR an API key
    (Authorization: Bearer sk_sonus_... / x-api-key — programmatic access).
    Works for Request AND WebSocket (both expose .cookies/.headers). Returns
    None when accounts mode is off."""
    if not accounts_enabled() or not connected():
        return None
    from src.accounts import apikeys
    key = apikeys.extract_from_headers(request.headers)
    if key:
        return await apikeys.user_for_key(key)
    return await repo.user_for_session(request.cookies.get(SESSION_COOKIE, ""))


def is_admin(user: dict | None) -> bool:
    """Platform operator? Declared by email in ADMIN_EMAILS (no separate
    login system — rides on the normal session/key auth)."""
    if not user:
        return False
    emails = {e.strip().lower() for e in
              (settings.admin_emails or "").split(",") if e.strip()}
    return (user.get("email") or "").lower() in emails


async def request_workspace(request: Request, user: dict) -> tuple[str | None, JSONResponse | None]:
    """Resolve + authorize the X-Workspace-Id header. 404 (not 403) on a
    workspace the user isn't in — don't confirm foreign ids exist."""
    ws = (request.headers.get("x-workspace-id") or "").strip()
    if not ws:
        return None, JSONResponse({"error": "X-Workspace-Id header required"}, status_code=400)
    if not await repo.is_member(ws, user["id"]):
        return None, JSONResponse({"error": "not found"}, status_code=404)
    return ws, None


async def _login_and_redirect(user_row: dict, nxt: str, request: Request) -> RedirectResponse:
    await repo.ensure_default_workspace(user_row)
    # First sign-in: wallet + the free trial (exactly once, guarded in-db).
    from src.accounts import billing
    await billing.ensure_wallet_with_trial(user_row["id"])
    token = await repo.create_session(user_row["id"])
    resp = RedirectResponse(_safe_next(nxt), status_code=303)
    _set_session_cookie(resp, token, request)
    resp.delete_cookie(_STATE_COOKIE, path="/")
    return resp


# ─── routes ───

@router.get("/auth/me")
async def auth_me(request: Request):
    tel = telephony_enabled()
    if not accounts_enabled():
        return {"enabled": False, "user": None, "workspaces": [], "telephony": tel}
    user = await current_user(request)
    if user is None:
        return {"enabled": True, "user": None, "workspaces": [], "telephony": tel}
    public = {k: user[k] for k in ("id", "email", "name", "picture")}
    return {"enabled": True, "user": public, "is_admin": is_admin(user),
            "telephony": tel, "workspaces": await repo.workspaces_for(user["id"])}


@router.get("/auth/google/login")
async def google_login(request: Request, next: str = ""):
    if not accounts_enabled():
        return JSONResponse({"error": "accounts disabled"}, status_code=404)
    import time as _t
    if not _login_bucket.allow(_t.monotonic()):
        return JSONResponse({"error": "too many attempts, try again shortly"},
                            status_code=429)
    if not settings.google_client_id:
        return JSONResponse(
            {"error": "Google sign-in not configured (GOOGLE_CLIENT_ID missing)"},
            status_code=503)
    nonce = secrets.token_urlsafe(24)
    state = _signer().dumps({"n": nonce, "next": _safe_next(next)})
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": f"{_base_url(request)}/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    resp = RedirectResponse(f"{_GOOGLE_AUTH}?{urlencode(params)}", status_code=303)
    resp.set_cookie(_STATE_COOKIE, nonce, max_age=_STATE_MAX_AGE, httponly=True,
                    samesite="lax", secure=_cookie_secure(request), path="/")
    return resp


@router.get("/auth/google/callback")
async def google_callback(request: Request, code: str = "", state: str = ""):
    if not accounts_enabled():
        return JSONResponse({"error": "accounts disabled"}, status_code=404)
    try:
        payload = _signer().loads(state, max_age=_STATE_MAX_AGE)
    except BadSignature:
        return JSONResponse({"error": "invalid state"}, status_code=400)
    if payload.get("n") != request.cookies.get(_STATE_COOKIE):
        return JSONResponse({"error": "state mismatch"}, status_code=400)
    if not code:
        return JSONResponse({"error": "missing code"}, status_code=400)

    async with httpx.AsyncClient(timeout=15) as client:
        tok = await client.post(_GOOGLE_TOKEN, data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": f"{_base_url(request)}/auth/google/callback",
            "grant_type": "authorization_code",
        })
        if tok.status_code != 200:
            logger.error("oauth.token_exchange_failed", status=tok.status_code,
                         body=tok.text[:200])
            return JSONResponse({"error": "token exchange failed"}, status_code=502)
        access_token = tok.json().get("access_token", "")
        ui = await client.get(_GOOGLE_USERINFO,
                              headers={"Authorization": f"Bearer {access_token}"})
        if ui.status_code != 200:
            return JSONResponse({"error": "userinfo failed"}, status_code=502)
        info = ui.json()

    if not info.get("sub") or not info.get("email"):
        return JSONResponse({"error": "incomplete Google profile"}, status_code=502)
    user_row = await repo.upsert_user(
        google_sub=info["sub"], email=info["email"],
        name=info.get("name") or "", picture=info.get("picture") or "")
    logger.info("auth.login", email=info["email"])
    return await _login_and_redirect(user_row, payload.get("next", "/console"), request)


@router.get("/auth/dev-login")
async def dev_login(request: Request, email: str = "", name: str = "", next: str = ""):
    """LOCAL TESTING ONLY (DEV_LOGIN_ENABLED=true): sign in without Google."""
    if not accounts_enabled() or not settings.dev_login_enabled:
        return JSONResponse({"error": "not found"}, status_code=404)
    import time as _t
    if not _login_bucket.allow(_t.monotonic()):
        return JSONResponse({"error": "too many attempts, try again shortly"},
                            status_code=429)
    email = (email or "dev@example.com").strip().lower()
    user_row = await repo.upsert_user(
        google_sub=f"dev:{email}", email=email,
        name=name or email.split("@")[0].title(), picture="")
    logger.warning("auth.dev_login", email=email)
    return await _login_and_redirect(user_row, next, request)


@router.post("/auth/logout")
async def logout(request: Request):
    if accounts_enabled() and connected():
        await repo.delete_session(request.cookies.get(SESSION_COOKIE, ""))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


# ─── workspaces ───

@router.get("/workspaces")
async def list_workspaces(request: Request):
    user = await current_user(request)
    if user is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return {"workspaces": await repo.workspaces_for(user["id"])}


@router.post("/workspaces")
async def create_workspace_route(request: Request):
    user = await current_user(request)
    if user is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name or len(name) > 60:
        return JSONResponse({"error": "name required (max 60 chars)"}, status_code=400)
    ws = await repo.create_workspace(name=name, owner_user_id=user["id"])
    ws["role"] = "owner"
    return JSONResponse(ws, status_code=201)


@router.patch("/workspaces/{workspace_id}")
async def rename_workspace_route(workspace_id: str, request: Request):
    user = await current_user(request)
    if user is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name or len(name) > 60:
        return JSONResponse({"error": "name required (max 60 chars)"}, status_code=400)
    ws = await repo.rename_workspace(workspace_id, user["id"], name)
    if ws is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return ws
