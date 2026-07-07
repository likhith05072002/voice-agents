"""/billing/* and /api-keys routes.

Top-up flow (Razorpay, env-gated):
  POST /billing/topup/order  -> create a provider order (amount in paise)
  ...browser runs Razorpay checkout with that order...
  POST /billing/topup/verify -> verify the HMAC signature, flip the payment
                                row created->paid INSIDE a transaction (double
                                -credit proof), credit the wallet.
Without RAZORPAY keys configured the order endpoint returns 503 and, in dev
(DEV_LOGIN_ENABLED), POST /billing/topup/dev credits instantly for testing.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

import httpx
import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.accounts import apikeys, billing
from src.accounts.auth import current_user
from src.accounts.db import pool
from src.config import settings

logger = structlog.get_logger()
router = APIRouter()

MIN_TOPUP_PAISE = 5_000        # ₹50
MAX_TOPUP_PAISE = 10_000_000   # ₹1,00,000


async def _user_or_401(request: Request):
    user = await current_user(request)
    if user is None:
        return None, JSONResponse({"error": "unauthorized"}, status_code=401)
    return user, None


# ─── wallet ───

@router.get("/billing/wallet")
async def wallet(request: Request):
    user, err = await _user_or_401(request)
    if err:
        return err
    bal = await billing.balance(user["id"])
    return {
        "balance_paise": bal,
        "seconds_left": billing.seconds_for(bal),
        "rate_paise_per_min": billing.rate_paise_per_min(),
        "trial_minutes": settings.trial_minutes,
        "payments_enabled": bool(settings.razorpay_key_id),
        "dev_topup": settings.dev_login_enabled,
        "ledger": await billing.ledger(user["id"], limit=50),
    }


@router.post("/billing/topup/dev")
async def topup_dev(request: Request):
    """LOCAL TESTING ONLY: instant credit without a payment provider."""
    if not settings.dev_login_enabled:
        return JSONResponse({"error": "not found"}, status_code=404)
    user, err = await _user_or_401(request)
    if err:
        return err
    body = await request.json()
    paise = int(body.get("amount_paise") or 0)
    if not (MIN_TOPUP_PAISE <= paise <= MAX_TOPUP_PAISE):
        return JSONResponse({"error": "amount out of range"}, status_code=400)
    bal = await billing.topup(user["id"], paise, ref=f"dev:{secrets.token_hex(4)}")
    return {"balance_paise": bal}


@router.post("/billing/topup/order")
async def topup_order(request: Request):
    user, err = await _user_or_401(request)
    if err:
        return err
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        return JSONResponse({"error": "payments not configured yet"}, status_code=503)
    body = await request.json()
    paise = int(body.get("amount_paise") or 0)
    if not (MIN_TOPUP_PAISE <= paise <= MAX_TOPUP_PAISE):
        return JSONResponse({"error": "amount out of range"}, status_code=400)
    async with httpx.AsyncClient(timeout=15,
                                 auth=(settings.razorpay_key_id,
                                       settings.razorpay_key_secret)) as client:
        r = await client.post("https://api.razorpay.com/v1/orders", json={
            "amount": paise, "currency": "INR",
            "notes": {"user_id": user["id"], "purpose": "sonuslabs-credits"},
        })
    if r.status_code != 200:
        logger.error("razorpay.order_failed", status=r.status_code, body=r.text[:200])
        return JSONResponse({"error": "payment provider error"}, status_code=502)
    order = r.json()
    await pool().execute(
        """INSERT INTO payments (user_id, provider, order_id, amount_paise, status)
           VALUES ($1::uuid, 'razorpay', $2, $3, 'created')""",
        user["id"], order["id"], paise)
    return {"order_id": order["id"], "amount_paise": paise,
            "key_id": settings.razorpay_key_id, "currency": "INR"}


@router.post("/billing/topup/verify")
async def topup_verify(request: Request):
    user, err = await _user_or_401(request)
    if err:
        return err
    body = await request.json()
    oid = body.get("razorpay_order_id") or ""
    pid = body.get("razorpay_payment_id") or ""
    sig = body.get("razorpay_signature") or ""
    expected = hmac.new(settings.razorpay_key_secret.encode(),
                        f"{oid}|{pid}".encode(), hashlib.sha256).hexdigest()
    if not (oid and pid and hmac.compare_digest(expected, sig)):
        return JSONResponse({"error": "signature verification failed"}, status_code=400)
    async with pool().acquire() as con:
        async with con.transaction():
            # created -> paid exactly once; a replayed verify matches 0 rows.
            row = await con.fetchrow(
                """UPDATE payments SET status = 'paid', payment_id = $3, paid_at = now()
                   WHERE order_id = $1 AND user_id = $2::uuid AND status = 'created'
                   RETURNING amount_paise""", oid, user["id"], pid)
    if row is None:
        return JSONResponse({"error": "order not found or already processed"},
                            status_code=409)
    bal = await billing.topup(user["id"], row["amount_paise"], ref=f"razorpay:{pid}")
    logger.info("billing.topup_paid", user=user["id"], paise=row["amount_paise"])
    return {"balance_paise": bal}


# ─── API keys ───

@router.get("/api-keys")
async def list_keys(request: Request):
    user, err = await _user_or_401(request)
    if err:
        return err
    return {"keys": await apikeys.list_for(user["id"])}


@router.post("/api-keys")
async def create_key(request: Request):
    user, err = await _user_or_401(request)
    if err:
        return err
    body = await request.json()
    name = (body.get("name") or "").strip() or "API key"
    existing = await apikeys.list_for(user["id"])
    if len(existing) >= 10:
        return JSONResponse({"error": "key limit reached (10)"}, status_code=400)
    made = await apikeys.create(user["id"], name)
    return JSONResponse(made, status_code=201)   # includes raw key — shown ONCE


@router.delete("/api-keys/{key_id}")
async def revoke_key(key_id: str, request: Request):
    user, err = await _user_or_401(request)
    if err:
        return err
    try:
        ok = await apikeys.revoke(user["id"], key_id)
    except Exception:   # malformed uuid
        ok = False
    if not ok:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"revoked": key_id}
