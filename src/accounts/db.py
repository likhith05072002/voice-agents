"""Shared asyncpg pool + a tiny ordered migration runner.

One pool per process (the deployment is single-process on the Pi). Migrations
are numbered ``migrations/pg/NNNN_*.sql`` files applied in order inside a
transaction each, tracked in ``schema_migrations``, and guarded by a Postgres
advisory lock so two processes starting at once can't race the DDL.
"""

from __future__ import annotations

from pathlib import Path

import asyncpg
import structlog

logger = structlog.get_logger()

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations" / "pg"
_ADVISORY_LOCK = 727001  # arbitrary app-wide constant for migration mutex

_pool: asyncpg.Pool | None = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("accounts db not connected (DATABASE_URL mode only)")
    return _pool


def connected() -> bool:
    return _pool is not None


async def connect(dsn: str) -> None:
    """Create the pool and bring the schema up to date. Called from lifespan."""
    global _pool
    if _pool is not None:
        return
    # Modest pool: the Pi is one process and Postgres default max_connections
    # is 100 — leave room for psql/admin sessions.
    _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=8,
                                      command_timeout=30)
    await _run_migrations(_pool)


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def _run_migrations(p: asyncpg.Pool) -> None:
    files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    async with p.acquire() as con:
        await con.execute("SELECT pg_advisory_lock($1)", _ADVISORY_LOCK)
        try:
            await con.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
            done = {r["version"] for r in
                    await con.fetch("SELECT version FROM schema_migrations")}
            for f in files:
                if f.name in done:
                    continue
                async with con.transaction():
                    await con.execute(f.read_text(encoding="utf-8"))
                    await con.execute(
                        "INSERT INTO schema_migrations (version) VALUES ($1)", f.name)
                logger.info("migrations.applied", version=f.name)
        finally:
            await con.execute("SELECT pg_advisory_unlock($1)", _ADVISORY_LOCK)
