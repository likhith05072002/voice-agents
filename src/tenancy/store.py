"""Agent resolution store.

Resolves an inbound (or outbound) call to the right business by explicit
``agent_id`` or dialed number (DID), falling back to a configured default. The
in-memory implementation is the interface other stores (DB/Redis) match later —
the call path only ever calls ``resolve``.
"""

from __future__ import annotations

import json

from src.tenancy.agents import AgentConfig, normalize_number


class AgentStore:
    def __init__(self, agents: list[AgentConfig] | None = None, default_id: str | None = None):
        self._by_id: dict[str, AgentConfig] = {}
        self._by_phone: dict[str, str] = {}
        agents = agents or []
        for a in agents:
            self.add(a)
        self._default_id = default_id or (agents[0].agent_id if agents else None)

    def add(self, agent: AgentConfig) -> None:
        self._by_id[agent.agent_id] = agent
        for n in agent.normalized_numbers():
            self._by_phone[n] = agent.agent_id

    def all(self) -> list[AgentConfig]:
        return list(self._by_id.values())

    def update(self, agent: AgentConfig) -> None:
        """Replace an agent and rebuild the phone index (numbers may have changed)."""
        self._by_id[agent.agent_id] = agent
        self._reindex()

    def remove(self, agent_id: str) -> bool:
        if agent_id not in self._by_id:
            return False
        del self._by_id[agent_id]
        self._reindex()
        return True

    def _reindex(self) -> None:
        self._by_phone = {}
        for a in self._by_id.values():
            for n in a.normalized_numbers():
                self._by_phone[n] = a.agent_id

    def get(self, agent_id: str) -> AgentConfig | None:
        return self._by_id.get(agent_id)

    def by_phone(self, number: str) -> AgentConfig | None:
        n = normalize_number(number)
        if not n:
            return None
        aid = self._by_phone.get(n)
        if aid is None:
            last10 = n[-10:]                    # tolerate +country-code variance
            for pn, candidate in self._by_phone.items():
                if pn[-10:] == last10:
                    aid = candidate
                    break
        return self._by_id.get(aid) if aid else None

    @property
    def default_id(self) -> str | None:
        return self._default_id

    def default(self) -> AgentConfig | None:
        return self._by_id.get(self._default_id) if self._default_id else None

    def resolve(self, *, agent_id: str | None = None, to_number: str | None = None) -> AgentConfig:
        """Resolve by explicit id, then dialed number, then default.

        Raises ``LookupError`` only when nothing matches AND no default exists —
        a misconfiguration we want to fail loudly on, not serve a wrong persona."""
        if agent_id:
            a = self.get(agent_id)
            if a:
                return a
        if to_number:
            a = self.by_phone(to_number)
            if a:
                return a
        d = self.default()
        if d is None:
            raise LookupError("no agent matched and no default configured")
        return d

    def __len__(self) -> int:
        return len(self._by_id)


def load_agents_json(path: str) -> list[AgentConfig]:
    """Load a list of agent configs from a JSON file (array of objects)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("agents JSON must be an array of agent objects")
    return [AgentConfig.from_dict(d) for d in data]
