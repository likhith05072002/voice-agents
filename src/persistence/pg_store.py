"""Postgres call store — drop-in for ``SqliteCallStore`` plus tenant filtering.

Implements the same CallStore surface (save/recent/search/stats) with an extra
optional ``workspace_id`` filter on the read methods. The workspace tag rides
in ``CallRecord.metadata["workspace_id"]`` (set at recorder construction from
the resolved agent) and is lifted into a real indexed column on save — the
CallRecord dataclass/row shape used by the SQLite store stays untouched.
"""

from __future__ import annotations

import json

from src.accounts.db import pool
from src.persistence.records import CallRecord, CallStats
from src.persistence.store import _COLUMNS, _round1


class PostgresCallStore:
    async def save(self, record: CallRecord) -> None:
        row = record.to_row()
        ws = None
        try:
            ws = (json.loads(row.get("metadata") or "{}") or {}).get("workspace_id") or None
        except Exception:
            ws = None
        cols = ["workspace_id", *_COLUMNS]
        vals = [ws, *(row[c] for c in _COLUMNS)]
        ph = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
        collist = ", ".join(cols)
        sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "call_id")
        if row.get("call_id"):
            # Real carrier call id: upsert (summary pass re-saves the record).
            await pool().execute(
                f"""INSERT INTO calls ({collist}) VALUES ({ph})
                    ON CONFLICT (call_id) WHERE call_id <> '' DO UPDATE SET {sets}""",
                *vals)
        else:
            await pool().execute(
                f"INSERT INTO calls ({collist}) VALUES ({ph})", *vals)

    # ─── reads ───

    @staticmethod
    def _where(agent_id: str | None, workspace_id: str | None,
               since: float | None = None, extra: str | None = None) -> tuple[str, list]:
        where, params = [], []
        if workspace_id:
            params.append(workspace_id)
            where.append(f"workspace_id = ${len(params)}::uuid")
        if agent_id:
            params.append(agent_id)
            where.append(f"agent_id = ${len(params)}")
        if since is not None:
            params.append(since)
            where.append(f"started_at >= ${len(params)}")
        if extra:
            where.append(extra)
        return (" WHERE " + " AND ".join(where)) if where else "", params

    async def recent(self, *, agent_id: str | None = None, limit: int = 50,
                     workspace_id: str | None = None) -> list[CallRecord]:
        wsql, params = self._where(agent_id, workspace_id)
        params.append(limit)
        rows = await pool().fetch(
            f"SELECT {', '.join(_COLUMNS)} FROM calls{wsql} "
            f"ORDER BY started_at DESC LIMIT ${len(params)}", *params)
        return [CallRecord.from_row(dict(r)) for r in rows]

    async def search(self, q: str, *, agent_id: str | None = None, limit: int = 50,
                     workspace_id: str | None = None) -> list[CallRecord]:
        if not q:
            return []
        wsql, params = self._where(agent_id, workspace_id)
        params.append(f"%{q}%")
        like = f"turns ILIKE ${len(params)}"
        wsql = (wsql + " AND " + like) if wsql else (" WHERE " + like)
        params.append(limit)
        rows = await pool().fetch(
            f"SELECT {', '.join(_COLUMNS)} FROM calls{wsql} "
            f"ORDER BY started_at DESC LIMIT ${len(params)}", *params)
        # ILIKE over the JSON blob can match keys — re-verify against turn text.
        ql = q.lower()
        out = [CallRecord.from_row(dict(r)) for r in rows]
        return [r for r in out if any(ql in t.text.lower() for t in r.turns)]

    async def stats(self, *, agent_id: str | None = None, since: float | None = None,
                    workspace_id: str | None = None) -> CallStats:
        wsql, params = self._where(agent_id, workspace_id, since)
        row = await pool().fetchrow(
            f"SELECT COUNT(*) AS n, AVG(duration_s) AS d, AVG(avg_perceived_ms) AS p "
            f"FROM calls{wsql}", *params)
        outcomes = await pool().fetch(
            f"SELECT COALESCE(NULLIF(outcome,''),'unknown') AS k, COUNT(*) AS c "
            f"FROM calls{wsql} GROUP BY 1", *params)
        return CallStats(
            agent_id=agent_id, total_calls=row["n"] or 0,
            avg_duration_s=_round1(row["d"]), avg_perceived_ms=_round1(row["p"]),
            by_outcome={r["k"]: r["c"] for r in outcomes})
