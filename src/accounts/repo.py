"""Users, workspaces, and sessions — the account-side data access.

All functions take the shared pool from ``src.accounts.db``. Rows come back as
plain dicts (uuid columns stringified) so route handlers can JSON them directly.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from src.accounts.db import pool

SESSION_TTL_DAYS = 30
# Sliding refresh: bump expiry when the session was last touched over a day ago
# (one UPDATE per day per user, not one per request).
SESSION_REFRESH_AFTER_H = 24


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _u(row) -> dict:
    d = dict(row)
    for k, v in d.items():
        if hasattr(v, "hex") and not isinstance(v, (bytes, str)):  # uuid.UUID
            d[k] = str(v)
        elif isinstance(v, datetime):
            d[k] = v.timestamp()
    return d


# ─── users ───

async def upsert_user(*, google_sub: str, email: str, name: str, picture: str) -> dict:
    row = await pool().fetchrow(
        """INSERT INTO users (google_sub, email, name, picture)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (google_sub) DO UPDATE
           SET email = EXCLUDED.email, name = EXCLUDED.name,
               picture = EXCLUDED.picture, last_login_at = now()
           RETURNING *""",
        google_sub, email, name, picture)
    return _u(row)


async def get_user(user_id: str) -> dict | None:
    row = await pool().fetchrow("SELECT * FROM users WHERE id = $1::uuid", user_id)
    return _u(row) if row else None


# ─── workspaces ───

async def create_workspace(*, name: str, owner_user_id: str) -> dict:
    async with pool().acquire() as con:
        async with con.transaction():
            ws = await con.fetchrow(
                "INSERT INTO workspaces (name, owner_user_id) VALUES ($1, $2::uuid) RETURNING *",
                name, owner_user_id)
            await con.execute(
                "INSERT INTO workspace_members (workspace_id, user_id, role) "
                "VALUES ($1, $2::uuid, 'owner')", ws["id"], owner_user_id)
    return _u(ws)


async def workspaces_for(user_id: str) -> list[dict]:
    rows = await pool().fetch(
        """SELECT w.id, w.name, w.owner_user_id, w.created_at, m.role
           FROM workspaces w JOIN workspace_members m ON m.workspace_id = w.id
           WHERE m.user_id = $1::uuid ORDER BY w.created_at""",
        user_id)
    return [_u(r) for r in rows]


async def is_member(workspace_id: str, user_id: str) -> bool:
    try:
        row = await pool().fetchrow(
            "SELECT 1 FROM workspace_members WHERE workspace_id = $1::uuid AND user_id = $2::uuid",
            workspace_id, user_id)
    except Exception:   # malformed uuid in the header -> not a member
        return False
    return row is not None


async def rename_workspace(workspace_id: str, user_id: str, name: str) -> dict | None:
    row = await pool().fetchrow(
        """UPDATE workspaces SET name = $3
           WHERE id = $1::uuid AND owner_user_id = $2::uuid RETURNING *""",
        workspace_id, user_id, name)
    return _u(row) if row else None


async def ensure_default_workspace(user: dict) -> None:
    """First login: give the user a workspace named after them."""
    existing = await workspaces_for(user["id"])
    if existing:
        return
    first = (user.get("name") or user.get("email") or "My").split(" ")[0].split("@")[0]
    await create_workspace(name=f"{first}'s workspace", owner_user_id=user["id"])


# ─── sessions ───

async def create_session(user_id: str) -> str:
    """Returns the RAW token for the cookie; only its hash is stored."""
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
    await pool().execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES ($1, $2::uuid, $3)",
        _hash(token), user_id, expires)
    # Opportunistic GC: logins are rare enough that sweeping expired sessions
    # here keeps the table bounded without a scheduler.
    await pool().execute("DELETE FROM sessions WHERE expires_at < now()")
    return token


async def user_for_session(token: str) -> dict | None:
    if not token:
        return None
    row = await pool().fetchrow(
        """SELECT u.*, s.token_hash, s.last_seen_at AS _seen
           FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token_hash = $1 AND s.expires_at > now()""",
        _hash(token))
    if row is None:
        return None
    seen = row["_seen"]
    if datetime.now(timezone.utc) - seen > timedelta(hours=SESSION_REFRESH_AFTER_H):
        await pool().execute(
            "UPDATE sessions SET last_seen_at = now(), expires_at = now() + $2::interval "
            "WHERE token_hash = $1", row["token_hash"], f"{SESSION_TTL_DAYS} days")
    d = _u(row)
    d.pop("token_hash", None)
    d.pop("_seen", None)
    return d


async def delete_session(token: str) -> None:
    if token:
        await pool().execute("DELETE FROM sessions WHERE token_hash = $1", _hash(token))
