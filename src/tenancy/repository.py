"""Durable agent storage + a manager that keeps the live store in sync.

Onboarding hundreds of businesses can't mean redeploying a JSON file. The
``SqliteAgentRepository`` persists each ``AgentConfig`` as a row; ``AgentManager``
hydrates the in-memory ``AgentStore`` at startup and mirrors every create/update/
delete to both, so resolution stays O(1) while changes survive restarts.

Each agent is stored as a single JSON blob, so adding config fields never needs a
schema migration. Writes run in a thread (rare admin ops, but never block the
loop). A Postgres repository is a drop-in implementing the same async methods.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

from src.tenancy.agents import AgentConfig
from src.tenancy.store import AgentStore


class SqliteAgentRepository:
    def __init__(self, path: str = "agents.db"):
        self.path = path
        self._init()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=5.0)
        con.execute("PRAGMA busy_timeout=5000")
        return con

    def _init(self) -> None:
        with self._connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("CREATE TABLE IF NOT EXISTS agents (agent_id TEXT PRIMARY KEY, json TEXT)")

    def _save_sync(self, agent: AgentConfig) -> None:
        with self._connect() as con:
            con.execute("INSERT OR REPLACE INTO agents (agent_id, json) VALUES (?, ?)",
                        (agent.agent_id, json.dumps(agent.to_dict(), ensure_ascii=False)))

    def _delete_sync(self, agent_id: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM agents WHERE agent_id=?", (agent_id,))

    def _load_all_sync(self) -> list[dict]:
        with self._connect() as con:
            return [json.loads(r[0]) for r in con.execute("SELECT json FROM agents")]

    async def save(self, agent: AgentConfig) -> None:
        await asyncio.to_thread(self._save_sync, agent)

    async def delete(self, agent_id: str) -> None:
        await asyncio.to_thread(self._delete_sync, agent_id)

    async def load_all(self) -> list[AgentConfig]:
        rows = await asyncio.to_thread(self._load_all_sync)
        return [AgentConfig.from_dict(d) for d in rows]


class AgentManager:
    """CRUD facade over the persistent repo + the live in-memory store."""

    def __init__(self, store: AgentStore, repo: SqliteAgentRepository):
        self.store = store
        self.repo = repo

    async def hydrate(self) -> int:
        """Load persisted agents into the live store at startup."""
        n = 0
        for a in await self.repo.load_all():
            self.store.add(a)
            n += 1
        return n

    def get(self, agent_id: str) -> AgentConfig | None:
        return self.store.get(agent_id)

    def list(self) -> list[AgentConfig]:
        return self.store.all()

    async def create(self, agent: AgentConfig) -> AgentConfig:
        if self.store.get(agent.agent_id) is not None:
            raise ValueError(f"agent '{agent.agent_id}' already exists")
        await self.repo.save(agent)
        self.store.add(agent)
        return agent

    async def update(self, agent: AgentConfig) -> AgentConfig:
        if self.store.get(agent.agent_id) is None:
            raise KeyError(agent.agent_id)
        await self.repo.save(agent)
        self.store.update(agent)
        return agent

    async def delete(self, agent_id: str) -> bool:
        removed = self.store.remove(agent_id)
        if removed:
            await self.repo.delete(agent_id)
        return removed
