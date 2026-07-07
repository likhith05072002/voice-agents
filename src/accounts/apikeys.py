"""API keys — programmatic access to the platform (ElevenLabs-style).

Format ``sk_sonus_<40 urlsafe chars>``. Only the sha256 lands in Postgres; the
raw key is returned exactly once at creation. Auth accepts the key via
``Authorization: Bearer sk_sonus_...`` or ``x-api-key`` (HTTP), or ``?api_key=``
on the web-call WebSocket (browser WS can't set headers).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime

from src.accounts.db import pool

KEY_PREFIX = "sk_sonus_"


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _row(r) -> dict:
    d = dict(r)
    for k, v in d.items():
        if hasattr(v, "hex") and not isinstance(v, (bytes, str)):
            d[k] = str(v)
        elif isinstance(v, datetime):
            d[k] = v.timestamp()
    return d


async def create(user_id: str, name: str) -> dict:
    """Returns the key row PLUS the raw key (the only time it exists)."""
    raw = KEY_PREFIX + secrets.token_urlsafe(30)
    row = await pool().fetchrow(
        """INSERT INTO api_keys (user_id, name, key_prefix, key_hash)
           VALUES ($1::uuid, $2, $3, $4) RETURNING id, name, key_prefix, created_at""",
        user_id, name[:60], raw[:14] + "…", _hash(raw))
    out = _row(row)
    out["key"] = raw
    return out


async def list_for(user_id: str) -> list[dict]:
    rows = await pool().fetch(
        """SELECT id, name, key_prefix, created_at, last_used_at
           FROM api_keys WHERE user_id = $1::uuid AND revoked_at IS NULL
           ORDER BY created_at DESC""", user_id)
    return [_row(r) for r in rows]


async def revoke(user_id: str, key_id: str) -> bool:
    r = await pool().execute(
        """UPDATE api_keys SET revoked_at = now()
           WHERE id = $1::uuid AND user_id = $2::uuid AND revoked_at IS NULL""",
        key_id, user_id)
    return r.endswith("1")


async def user_for_key(raw_key: str) -> dict | None:
    """Resolve a presented key to its (non-revoked) owner. Touches last_used_at
    at most ~once a minute to avoid a write per request."""
    if not raw_key or not raw_key.startswith(KEY_PREFIX):
        return None
    row = await pool().fetchrow(
        """SELECT u.*, k.id AS _kid, k.last_used_at AS _seen
           FROM api_keys k JOIN users u ON u.id = k.user_id
           WHERE k.key_hash = $1 AND k.revoked_at IS NULL""",
        _hash(raw_key))
    if row is None:
        return None
    seen = row["_seen"]
    if seen is None or (datetime.now(seen.tzinfo) - seen).total_seconds() > 60:
        await pool().execute(
            "UPDATE api_keys SET last_used_at = now() WHERE id = $1", row["_kid"])
    d = _row(row)
    d.pop("_kid", None)
    d.pop("_seen", None)
    return d


def extract_from_headers(headers) -> str:
    """Pull a key from Authorization: Bearer or x-api-key."""
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer ") and auth[7:].startswith(KEY_PREFIX):
        return auth[7:].strip()
    xk = headers.get("x-api-key", "")
    if xk.startswith(KEY_PREFIX):
        return xk.strip()
    return ""
