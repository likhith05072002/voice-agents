"""Pluggable call-record storage.

``CallStore`` is the interface the call path uses; nothing downstream cares which
backend is behind it. ``InMemoryCallStore`` is for tests/dev; ``SqliteCallStore``
is the zero-infra default for a single node. A Postgres/managed store is a
drop-in implementing the same two methods.

SQLite writes run in a thread (``asyncio.to_thread``) so persistence never blocks
the audio event loop, and each operation uses its own short-lived connection
(safe across the executor threads).
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Protocol

from src.persistence.records import CallRecord, CallStats

_COLUMNS = [
    "call_id", "agent_id", "from_number", "to_number", "started_at", "ended_at",
    "duration_s", "turn_count", "avg_perceived_ms", "outcome", "turns", "metrics",
    "metadata",
]


class CallStore(Protocol):
    async def save(self, record: CallRecord) -> None: ...
    async def recent(self, *, agent_id: str | None = None, limit: int = 50) -> list[CallRecord]: ...
    async def stats(self, *, agent_id: str | None = None, since: float | None = None) -> CallStats: ...


def _round1(x: float | None) -> float | None:
    return round(x, 1) if x is not None else None


class InMemoryCallStore:
    def __init__(self) -> None:
        self._rows: list[CallRecord] = []

    async def save(self, record: CallRecord) -> None:
        self._rows.append(record)

    async def recent(self, *, agent_id: str | None = None, limit: int = 50) -> list[CallRecord]:
        rows = [r for r in self._rows if agent_id is None or r.agent_id == agent_id]
        return list(reversed(rows))[:limit]

    async def search(self, q: str, *, agent_id: str | None = None,
                     limit: int = 50) -> list[CallRecord]:
        ql = (q or "").lower()
        if not ql:
            return []
        hits = [r for r in self._rows
                if (agent_id is None or r.agent_id == agent_id)
                and any(ql in t.text.lower() for t in r.turns)]
        return list(reversed(hits))[:limit]

    async def stats(self, *, agent_id: str | None = None, since: float | None = None) -> CallStats:
        rows = [r for r in self._rows
                if (agent_id is None or r.agent_id == agent_id)
                and (since is None or r.started_at >= since)]
        durs = [r.duration_s for r in rows if r.duration_s is not None]
        percs = [r.avg_perceived_ms() for r in rows if r.avg_perceived_ms() is not None]
        by_outcome: dict[str, int] = {}
        for r in rows:
            key = r.outcome or "unknown"
            by_outcome[key] = by_outcome.get(key, 0) + 1
        return CallStats(
            agent_id=agent_id, total_calls=len(rows),
            avg_duration_s=_round1(sum(durs) / len(durs)) if durs else None,
            avg_perceived_ms=_round1(sum(percs) / len(percs)) if percs else None,
            by_outcome=by_outcome,
        )

    def __len__(self) -> int:
        return len(self._rows)


class SqliteCallStore:
    def __init__(self, path: str = "calls.db") -> None:
        self.path = path
        self._init()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=5.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=5000")    # wait, don't error, on lock
        return con

    def _init(self) -> None:
        with self._connect() as con:
            # WAL lets readers (/calls analytics) run while a call is being
            # written, and avoids writer-vs-writer blocking across concurrent
            # calls on one node.
            con.execute("PRAGMA journal_mode=WAL")
            con.execute(
                """CREATE TABLE IF NOT EXISTS calls (
                    call_id TEXT PRIMARY KEY, agent_id TEXT, from_number TEXT,
                    to_number TEXT, started_at REAL, ended_at REAL, duration_s REAL,
                    turn_count INTEGER, avg_perceived_ms REAL, outcome TEXT,
                    turns TEXT, metrics TEXT, metadata TEXT)"""
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_calls_agent ON calls(agent_id, started_at)")

    def _save_sync(self, row: dict) -> None:
        cols = ",".join(_COLUMNS)
        ph = ",".join("?" for _ in _COLUMNS)
        with self._connect() as con:
            con.execute(f"INSERT OR REPLACE INTO calls ({cols}) VALUES ({ph})",
                        [row[c] for c in _COLUMNS])

    async def save(self, record: CallRecord) -> None:
        await asyncio.to_thread(self._save_sync, record.to_row())

    def _recent_sync(self, agent_id: str | None, limit: int) -> list[dict]:
        with self._connect() as con:
            if agent_id:
                cur = con.execute(
                    "SELECT * FROM calls WHERE agent_id=? ORDER BY started_at DESC LIMIT ?",
                    (agent_id, limit))
            else:
                cur = con.execute(
                    "SELECT * FROM calls ORDER BY started_at DESC LIMIT ?", (limit,))
            return [dict(r) for r in cur.fetchall()]

    async def recent(self, *, agent_id: str | None = None, limit: int = 50) -> list[CallRecord]:
        rows = await asyncio.to_thread(self._recent_sync, agent_id, limit)
        return [CallRecord.from_row(r) for r in rows]

    def _search_sync(self, q: str, agent_id: str | None, limit: int) -> list[dict]:
        # Escape LIKE wildcards in the user's query, then wrap it ourselves.
        esc = q.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{esc}%"
        where = ["turns LIKE ? ESCAPE '\\'"]
        params: list = [pattern]
        if agent_id:
            where.append("agent_id=?")
            params.append(agent_id)
        params.append(limit)
        with self._connect() as con:
            cur = con.execute(
                f"SELECT * FROM calls WHERE {' AND '.join(where)} "
                f"ORDER BY started_at DESC LIMIT ?", params)
            return [dict(r) for r in cur.fetchall()]

    async def search(self, q: str, *, agent_id: str | None = None,
                     limit: int = 50) -> list[CallRecord]:
        if not q:
            return []
        rows = await asyncio.to_thread(self._search_sync, q, agent_id, limit)
        # LIKE over the JSON blob can match role keys etc. — re-verify in Python.
        ql = q.lower()
        out = [CallRecord.from_row(r) for r in rows]
        return [r for r in out if any(ql in t.text.lower() for t in r.turns)]

    def _stats_sync(self, agent_id: str | None, since: float | None) -> tuple:
        where, params = [], []
        if agent_id:
            where.append("agent_id=?")
            params.append(agent_id)
        if since is not None:
            where.append("started_at>=?")
            params.append(since)
        wsql = (" WHERE " + " AND ".join(where)) if where else ""
        with self._connect() as con:
            total, avg_dur, avg_perc = con.execute(
                f"SELECT COUNT(*), AVG(duration_s), AVG(avg_perceived_ms) FROM calls{wsql}",
                params).fetchone()
            outcomes = con.execute(
                f"SELECT COALESCE(NULLIF(outcome,''),'unknown'), COUNT(*) FROM calls{wsql} "
                f"GROUP BY outcome", params).fetchall()
        return total, avg_dur, avg_perc, {k: v for k, v in outcomes}

    async def stats(self, *, agent_id: str | None = None, since: float | None = None) -> CallStats:
        total, avg_dur, avg_perc, by_outcome = await asyncio.to_thread(
            self._stats_sync, agent_id, since)
        return CallStats(agent_id=agent_id, total_calls=total or 0,
                         avg_duration_s=_round1(avg_dur), avg_perceived_ms=_round1(avg_perc),
                         by_outcome=by_outcome)
