"""Prepaid credits: wallet + ledger + per-second call metering.

Money is integer PAISE end to end. The platform rate is ₹/min (settings), so a
second costs rate/60 — charged on call end for the seconds actually used, and
enforced up front by deriving a max call duration from the balance (the same
watchdog mechanism as the public-demo cap, so a caller can never talk past
what the wallet covers).

All movements go through ``_apply()`` which updates the wallet AND appends a
ledger row atomically — the ledger is the audit trail, the wallet is a cache.
"""

from __future__ import annotations

import math

import structlog

from src.accounts.db import pool
from src.config import settings

logger = structlog.get_logger()


def rate_paise_per_min() -> int:
    return max(1, int(settings.rate_paise_per_min))


def cost_paise(seconds: float) -> int:
    """Whole seconds, rounded up, at rate/60 per second (ceil — never free)."""
    s = max(0, math.ceil(seconds))
    return math.ceil(s * rate_paise_per_min() / 60)


def seconds_for(balance_paise: int) -> int:
    """How long the balance lasts (floor — never promise unpayable seconds)."""
    if balance_paise <= 0:
        return 0
    return int(balance_paise * 60 // rate_paise_per_min())


async def _apply(con, user_id: str, delta: int, kind: str, ref: str = "",
                 seconds: float | None = None) -> int:
    """Atomically move credits and write the ledger row. Returns new balance."""
    row = await con.fetchrow(
        """UPDATE wallets SET balance_paise = balance_paise + $2, updated_at = now()
           WHERE user_id = $1::uuid RETURNING balance_paise""",
        user_id, delta)
    if row is None:   # wallet row missing (shouldn't happen post-signup) — create
        row = await con.fetchrow(
            """INSERT INTO wallets (user_id, balance_paise) VALUES ($1::uuid, $2)
               RETURNING balance_paise""", user_id, delta)
    bal = row["balance_paise"]
    await con.execute(
        """INSERT INTO credit_ledger (user_id, delta_paise, balance_after, kind, ref, seconds)
           VALUES ($1::uuid, $2, $3, $4, $5, $6)""",
        user_id, delta, bal, kind, ref, seconds)
    return bal


async def ensure_wallet_with_trial(user_id: str) -> None:
    """First sign-in: create the wallet and grant the free trial exactly once
    (trial_granted flips inside the same transaction — no double grants)."""
    trial_paise = seconds_to_paise_grant()
    async with pool().acquire() as con:
        async with con.transaction():
            await con.execute(
                "INSERT INTO wallets (user_id) VALUES ($1::uuid) ON CONFLICT DO NOTHING",
                user_id)
            row = await con.fetchrow(
                """UPDATE wallets SET trial_granted = true
                   WHERE user_id = $1::uuid AND trial_granted = false
                   RETURNING user_id""", user_id)
            if row is not None and trial_paise > 0:
                await _apply(con, user_id, trial_paise, "trial_grant",
                             ref=f"{settings.trial_minutes}min free trial")
                logger.info("billing.trial_granted", user=user_id,
                            paise=trial_paise)


def seconds_to_paise_grant() -> int:
    return settings.trial_minutes * rate_paise_per_min()


async def balance(user_id: str) -> int:
    row = await pool().fetchrow(
        "SELECT balance_paise FROM wallets WHERE user_id = $1::uuid", user_id)
    return row["balance_paise"] if row else 0


async def workspace_owner(workspace_id: str) -> str | None:
    row = await pool().fetchrow(
        "SELECT owner_user_id FROM workspaces WHERE id = $1::uuid", workspace_id)
    return str(row["owner_user_id"]) if row else None


async def balance_seconds_for_workspace(workspace_id: str) -> int:
    """Seconds of talk time the owning wallet can pay for right now."""
    owner = await workspace_owner(workspace_id)
    if owner is None:
        return 0
    return seconds_for(await balance(owner))


async def charge_workspace_usage(workspace_id: str, seconds: float, ref: str) -> None:
    """Bill a finished call to the workspace owner's wallet. Never raises —
    a metering failure must not break call teardown (it's logged loudly)."""
    try:
        owner = await workspace_owner(workspace_id)
        if owner is None:
            logger.error("billing.no_owner", workspace=workspace_id, ref=ref)
            return
        paise = cost_paise(seconds)
        if paise <= 0:
            return
        async with pool().acquire() as con:
            async with con.transaction():
                bal = await _apply(con, owner, -paise, "call_usage", ref=ref,
                                   seconds=round(seconds, 1))
        logger.info("billing.charged", user=owner, paise=paise,
                    seconds=round(seconds, 1), balance=bal, ref=ref)
    except Exception as e:  # noqa: BLE001
        logger.error("billing.charge_failed", workspace=workspace_id,
                     ref=ref, error=str(e))


async def charge_fixed(user_id: str, paise: int, kind: str, ref: str,
                       con=None) -> int | None:
    """Charge a fixed fee (e.g. monthly number rent) IF the balance covers it.
    Returns the new balance, or None when insufficient (nothing charged).

    Pass ``con`` to charge INSIDE an existing transaction — the number-claim
    flow needs the rent charge and the DID assignment to commit or roll back
    together (money moved without the number assigned is a support ticket)."""
    async def _run(c) -> int | None:
        row = await c.fetchrow(
            "SELECT balance_paise FROM wallets WHERE user_id = $1::uuid FOR UPDATE",
            user_id)
        if row is None or row["balance_paise"] < paise:
            return None
        return await _apply(c, user_id, -paise, kind, ref=ref)

    if con is not None:
        return await _run(con)
    async with pool().acquire() as c:
        async with c.transaction():
            return await _run(c)


async def topup(user_id: str, paise: int, ref: str) -> int:
    async with pool().acquire() as con:
        async with con.transaction():
            return await _apply(con, user_id, paise, "topup", ref=ref)


async def ledger(user_id: str, limit: int = 50) -> list[dict]:
    rows = await pool().fetch(
        """SELECT delta_paise, balance_after, kind, ref, seconds,
                  extract(epoch FROM created_at) AS t
           FROM credit_ledger WHERE user_id = $1::uuid
           ORDER BY id DESC LIMIT $2""", user_id, limit)
    return [dict(r) for r in rows]
