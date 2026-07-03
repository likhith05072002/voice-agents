"""Integration tests for the runtime agent admin API."""

from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)
H = {"x-api-key": "admin-secret"}


def test_admin_requires_auth():
    assert client.get("/agents").status_code == 401
    assert client.post("/agents", json={"agent_id": "x"}).status_code == 401


def test_agent_crud_lifecycle():
    # create
    r = client.post("/agents", headers=H, json={
        "agent_id": "acme", "name": "Acme", "phone_numbers": ["+15551110000"]})
    assert r.status_code == 201

    # appears in list + routes by phone via resolution (get by id)
    ids = [a["agent_id"] for a in client.get("/agents", headers=H).json()["agents"]]
    assert "acme" in ids
    assert client.get("/agents/acme", headers=H).json()["name"] == "Acme"

    # duplicate create -> 409
    assert client.post("/agents", headers=H, json={"agent_id": "acme"}).status_code == 409

    # partial update preserves untouched fields
    r = client.patch("/agents/acme", headers=H, json={"name": "Acme Corp"})
    assert r.status_code == 200
    assert r.json()["name"] == "Acme Corp"
    assert r.json()["phone_numbers"] == ["+15551110000"]

    # delete
    assert client.delete("/agents/acme", headers=H).status_code == 200
    assert client.get("/agents/acme", headers=H).status_code == 404


def test_cannot_delete_default_agent():
    assert client.delete("/agents/default", headers=H).status_code == 400


def test_patch_missing_is_404():
    assert client.patch("/agents/ghost", headers=H, json={"name": "x"}).status_code == 404


def test_analytics_and_calls_endpoints_respond():
    a = client.get("/analytics")
    assert a.status_code == 200 and "total_calls" in a.json()
    c = client.get("/calls")
    assert c.status_code == 200 and "calls" in c.json()


def test_created_agent_persists_to_repo(tmp_path):
    client.post("/agents", headers=H, json={"agent_id": "persist-me", "name": "P"})
    # a fresh repo on the same DB file sees it
    from src.tenancy.repository import SqliteAgentRepository
    import os
    repo = SqliteAgentRepository(os.environ["AGENTS_DB_PATH"])
    import asyncio
    loaded = asyncio.get_event_loop().run_until_complete(repo.load_all())
    assert any(a.agent_id == "persist-me" for a in loaded)
