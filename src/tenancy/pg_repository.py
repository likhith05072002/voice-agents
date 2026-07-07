"""Postgres agent repository — drop-in for ``SqliteAgentRepository``.

Same async surface (save/delete/load_all) so ``AgentManager`` doesn't change.
The whole AgentConfig still round-trips as one JSON blob (new fields never need
a migration); ``workspace_id`` is ALSO lifted into a real column so tenant
listing is an indexed filter, not a blob parse.
"""

from __future__ import annotations

import json

from src.accounts.db import pool
from src.tenancy.agents import AgentConfig


class PostgresAgentRepository:
    async def save(self, agent: AgentConfig) -> None:
        ws = agent.workspace_id or None   # '' -> NULL (platform agents)
        await pool().execute(
            """INSERT INTO agents (agent_id, workspace_id, json)
               VALUES ($1, $2::uuid, $3::jsonb)
               ON CONFLICT (agent_id) DO UPDATE
               SET workspace_id = EXCLUDED.workspace_id,
                   json = EXCLUDED.json, updated_at = now()""",
            agent.agent_id, ws, json.dumps(agent.to_dict(), ensure_ascii=False))

    async def delete(self, agent_id: str) -> None:
        await pool().execute("DELETE FROM agents WHERE agent_id = $1", agent_id)

    async def load_all(self) -> list[AgentConfig]:
        rows = await pool().fetch("SELECT workspace_id, json FROM agents")
        out = []
        for r in rows:
            d = json.loads(r["json"])
            # The column wins over the blob (server-controlled field).
            d["workspace_id"] = str(r["workspace_id"]) if r["workspace_id"] else ""
            out.append(AgentConfig.from_dict(d))
        return out
