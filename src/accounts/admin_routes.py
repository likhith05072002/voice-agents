"""/admin/* — the platform operator's monitor (cross-tenant, read-only).

Gated by ADMIN_EMAILS via ``is_admin``. Non-admins (and anonymous) get 404 —
the admin surface shouldn't even be discoverable. Everything here is a read;
operator MUTATIONS (adjusting wallets, banning users) stay deliberate manual
SQL until there's a policy for them.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.accounts.auth import current_user, is_admin
from src.accounts.db import pool

router = APIRouter()

_DAY_S = 86400


async def _admin_or_404(request: Request):
    user = await current_user(request)
    if not is_admin(user):
        return None, JSONResponse({"error": "not found"}, status_code=404)
    return user, None


@router.get("/admin/overview")
async def overview(request: Request):
    _, err = await _admin_or_404(request)
    if err:
        return err
    day_ago = time.time() - _DAY_S
    p = pool()
    users = await p.fetchval("SELECT COUNT(*) FROM users")
    workspaces = await p.fetchval("SELECT COUNT(*) FROM workspaces")
    agents = await p.fetchval("SELECT COUNT(*) FROM agents WHERE workspace_id IS NOT NULL")
    calls_total, secs_total = await p.fetchrow(
        "SELECT COUNT(*), COALESCE(SUM(duration_s),0) FROM calls")
    calls_24h, secs_24h = await p.fetchrow(
        "SELECT COUNT(*), COALESCE(SUM(duration_s),0) FROM calls WHERE started_at >= $1",
        day_ago)
    revenue_paid = await p.fetchval(
        "SELECT COALESCE(SUM(amount_paise),0) FROM payments WHERE status = 'paid'")
    credits_issued = await p.fetchval(
        "SELECT COALESCE(SUM(delta_paise),0) FROM credit_ledger WHERE kind IN ('topup','trial_grant')")
    usage_burned = await p.fetchval(
        "SELECT COALESCE(SUM(-delta_paise),0) FROM credit_ledger WHERE kind = 'call_usage'")
    outstanding = await p.fetchval("SELECT COALESCE(SUM(balance_paise),0) FROM wallets")
    new_users_24h = await p.fetchval(
        "SELECT COUNT(*) FROM users WHERE created_at >= now() - interval '24 hours'")
    api_keys = await p.fetchval(
        "SELECT COUNT(*) FROM api_keys WHERE revoked_at IS NULL")
    return {
        "users": users, "new_users_24h": new_users_24h,
        "workspaces": workspaces, "agents": agents, "api_keys": api_keys,
        "calls_total": calls_total, "calls_24h": calls_24h,
        "minutes_total": round((secs_total or 0) / 60, 1),
        "minutes_24h": round((secs_24h or 0) / 60, 1),
        "revenue_paid_paise": revenue_paid,
        "credits_issued_paise": credits_issued,
        "usage_burned_paise": usage_burned,
        "credits_outstanding_paise": outstanding,
    }


@router.get("/admin/users")
async def users(request: Request, limit: int = 200):
    _, err = await _admin_or_404(request)
    if err:
        return err
    rows = await pool().fetch(
        """SELECT u.id, u.email, u.name,
                  extract(epoch FROM u.created_at)   AS created_at,
                  extract(epoch FROM u.last_login_at) AS last_login_at,
                  COALESCE(w.balance_paise, 0) AS balance_paise,
                  (SELECT COUNT(*) FROM workspace_members m
                    WHERE m.user_id = u.id)                        AS workspaces,
                  (SELECT COUNT(*) FROM agents a
                    JOIN workspace_members m2 ON m2.workspace_id = a.workspace_id
                    WHERE m2.user_id = u.id)                       AS agents,
                  (SELECT COALESCE(SUM(-l.delta_paise), 0) FROM credit_ledger l
                    WHERE l.user_id = u.id AND l.kind = 'call_usage') AS spent_paise,
                  (SELECT COUNT(*) FROM api_keys k
                    WHERE k.user_id = u.id AND k.revoked_at IS NULL) AS api_keys
           FROM users u LEFT JOIN wallets w ON w.user_id = u.id
           ORDER BY u.created_at DESC LIMIT $1""", min(limit, 1000))
    return {"users": [{**dict(r), "id": str(r["id"])} for r in rows]}


@router.get("/admin/calls")
async def calls(request: Request, limit: int = 100):
    _, err = await _admin_or_404(request)
    if err:
        return err
    rows = await pool().fetch(
        """SELECT c.call_id, c.agent_id, c.from_number, c.to_number, c.started_at,
                  c.duration_s, c.turn_count, c.avg_perceived_ms, c.outcome,
                  ws.name AS workspace_name, u.email AS owner_email
           FROM calls c
           LEFT JOIN workspaces ws ON ws.id = c.workspace_id
           LEFT JOIN users u ON u.id = ws.owner_user_id
           ORDER BY c.started_at DESC NULLS LAST LIMIT $1""", min(limit, 500))
    return {"calls": [dict(r) for r in rows]}


@router.get("/admin/numbers")
async def numbers(request: Request):
    _, err = await _admin_or_404(request)
    if err:
        return err
    rows = await pool().fetch(
        """SELECT n.number, n.country, n.monthly_paise, n.status, n.agent_id,
                  n.notes, extract(epoch FROM n.assigned_at) AS assigned_at,
                  ws.name AS workspace_name, u.email AS owner_email
           FROM phone_numbers n
           LEFT JOIN workspaces ws ON ws.id = n.workspace_id
           LEFT JOIN users u ON u.id = ws.owner_user_id
           ORDER BY n.status, n.created_at DESC"""
    )
    return {"numbers": [dict(r) for r in rows]}


@router.post("/admin/numbers")
async def add_number(request: Request):
    """Operator stocks the pool (number bought on Telnyx / an Indian partner
    and already pointed at our Call Control app / webhook)."""
    _, err = await _admin_or_404(request)
    if err:
        return err
    body = await request.json()
    number = (body.get("number") or "").strip()
    if not number.startswith("+") or len(number) < 8:
        return JSONResponse({"error": "number must be E.164 (+...)"}, status_code=400)
    country = (body.get("country") or "US").strip().upper()
    monthly = int(body.get("monthly_paise") or 19900)
    notes = (body.get("notes") or "").strip()
    try:
        await pool().execute(
            """INSERT INTO phone_numbers (number, country, monthly_paise, notes)
               VALUES ($1, $2, $3, $4)""", number, country, monthly, notes)
    except Exception:
        return JSONResponse({"error": "number already in pool"}, status_code=409)
    return JSONResponse({"number": number, "country": country,
                         "monthly_paise": monthly}, status_code=201)


@router.delete("/admin/numbers/{number}")
async def remove_number(number: str, request: Request):
    _, err = await _admin_or_404(request)
    if err:
        return err
    r = await pool().execute(
        "DELETE FROM phone_numbers WHERE number = $1 AND status = 'available'", number)
    if not r.endswith("1"):
        return JSONResponse(
            {"error": "not found or currently assigned (release it first)"},
            status_code=409)
    return {"deleted": number}


@router.get("/admin/ledger")
async def ledger(request: Request, limit: int = 200):
    _, err = await _admin_or_404(request)
    if err:
        return err
    rows = await pool().fetch(
        """SELECT l.delta_paise, l.balance_after, l.kind, l.ref, l.seconds,
                  extract(epoch FROM l.created_at) AS t, u.email
           FROM credit_ledger l JOIN users u ON u.id = l.user_id
           ORDER BY l.id DESC LIMIT $1""", min(limit, 1000))
    return {"ledger": [dict(r) for r in rows]}
