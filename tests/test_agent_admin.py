"""Tests for runtime agent CRUD: store mutations, repository, manager."""

import pytest

from src.tenancy.agents import AgentConfig
from src.tenancy.store import AgentStore
from src.tenancy.repository import SqliteAgentRepository, AgentManager


# ─── store mutations ───

def test_update_rebuilds_phone_index():
    store = AgentStore([AgentConfig(agent_id="a", phone_numbers=["+1111111111"])],
                       default_id="a")
    store.update(AgentConfig(agent_id="a", phone_numbers=["+2222222222"]))
    assert store.by_phone("+1111111111") is None        # old number unrouted
    assert store.by_phone("+2222222222").agent_id == "a"


def test_remove_drops_agent_and_numbers():
    store = AgentStore([
        AgentConfig(agent_id="a", phone_numbers=["+1111111111"]),
        AgentConfig(agent_id="b", phone_numbers=["+2222222222"]),
    ], default_id="a")
    assert store.remove("b") is True
    assert store.get("b") is None
    assert store.by_phone("+2222222222") is None
    assert store.remove("missing") is False


# ─── repository roundtrip ───

async def test_repository_save_load_delete(tmp_path):
    repo = SqliteAgentRepository(str(tmp_path / "agents.db"))
    await repo.save(AgentConfig(agent_id="clinic", name="Clinic", phone_numbers=["+19999999999"]))
    loaded = await repo.load_all()
    assert len(loaded) == 1 and loaded[0].agent_id == "clinic"
    assert loaded[0].phone_numbers == ["+19999999999"]
    await repo.delete("clinic")
    assert await repo.load_all() == []


async def test_repository_is_durable_across_instances(tmp_path):
    path = str(tmp_path / "a.db")
    await SqliteAgentRepository(path).save(AgentConfig(agent_id="x", name="X"))
    again = await SqliteAgentRepository(path).load_all()
    assert again[0].name == "X"


# ─── manager sync ───

async def _manager(tmp_path):
    store = AgentStore([AgentConfig(agent_id="default")], default_id="default")
    repo = SqliteAgentRepository(str(tmp_path / "m.db"))
    return AgentManager(store, repo), store, repo


async def test_manager_create_persists_and_routes(tmp_path):
    mgr, store, repo = await _manager(tmp_path)
    await mgr.create(AgentConfig(agent_id="new", phone_numbers=["+15551112222"]))
    assert store.by_phone("+15551112222").agent_id == "new"      # live store updated
    assert any(a.agent_id == "new" for a in await repo.load_all())  # persisted


async def test_manager_create_rejects_duplicate(tmp_path):
    mgr, *_ = await _manager(tmp_path)
    await mgr.create(AgentConfig(agent_id="dup"))
    with pytest.raises(ValueError):
        await mgr.create(AgentConfig(agent_id="dup"))


async def test_manager_update_requires_existing(tmp_path):
    mgr, *_ = await _manager(tmp_path)
    with pytest.raises(KeyError):
        await mgr.update(AgentConfig(agent_id="ghost"))


async def test_manager_delete_syncs_both(tmp_path):
    mgr, store, repo = await _manager(tmp_path)
    await mgr.create(AgentConfig(agent_id="temp"))
    assert await mgr.delete("temp") is True
    assert store.get("temp") is None
    assert await repo.load_all() == []
    assert await mgr.delete("temp") is False


async def test_manager_hydrate_loads_persisted(tmp_path):
    mgr, store, repo = await _manager(tmp_path)
    await repo.save(AgentConfig(agent_id="seed", name="Seed"))
    n = await mgr.hydrate()
    assert n == 1 and store.get("seed").name == "Seed"
