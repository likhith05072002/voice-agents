"""Persistent per-agent voice cloning: auth, provisioning, the inworld-id
PATCH guard (no cross-tenant voice hijack through our key), and slot hygiene
on re-record / clone removal."""

import base64

import pytest
from fastapi.testclient import TestClient

import src.main as main
from src.main import app

client = TestClient(app)
H = {"x-api-key": "admin-secret"}

GOOD_B64 = base64.b64encode(b"\x00" * 150_000).decode()   # ~4.7s of 16k PCM16


@pytest.fixture()
def cloning_on(monkeypatch):
    """Enable cloning with a faked Inworld: records deletions, mints one id."""
    deleted: list[str] = []
    monkeypatch.setattr(main.settings, "inworld_api_key", "test-key")

    async def fake_clone(display_name, audio_b64, lang_code="EN_US", tags=None):
        return "fake-voice-1", None

    async def fake_delete(vid):
        deleted.append(vid)
        return True

    monkeypatch.setattr(main, "_inworld_clone", fake_clone)
    monkeypatch.setattr(main, "_inworld_delete_voice", fake_delete)
    return deleted


def _mk(agent_id: str) -> None:
    client.post("/agents", headers=H, json={"agent_id": agent_id, "name": "T"})


def test_clone_endpoint_requires_auth(cloning_on):
    _mk("vc-auth")
    r = client.post("/agents/vc-auth/voice-clone", json={"audio_b64": GOOD_B64})
    assert r.status_code == 401
    client.delete("/agents/vc-auth", headers=H)


def test_clone_sets_voice_and_rerecord_frees_old_slot(cloning_on):
    deleted = cloning_on
    _mk("vc-flow")
    r = client.post("/agents/vc-flow/voice-clone", headers=H,
                    json={"audio_b64": GOOD_B64})
    assert r.status_code == 200 and r.json()["voice"] == "inworld:fake-voice-1"
    assert (client.get("/agents/vc-flow", headers=H).json()["voice"]
            == "inworld:fake-voice-1")
    r = client.post("/agents/vc-flow/voice-clone", headers=H,
                    json={"audio_b64": GOOD_B64})
    assert r.status_code == 200
    assert deleted == ["fake-voice-1"]      # the replaced clone was freed
    client.delete("/agents/vc-flow", headers=H)


def test_patch_rejects_foreign_inworld_voice(cloning_on):
    _mk("vc-guard")
    client.post("/agents/vc-guard/voice-clone", headers=H,
                json={"audio_b64": GOOD_B64})
    r = client.patch("/agents/vc-guard", headers=H,
                     json={"voice": "inworld:stolen-voice"})
    assert r.status_code == 400
    # echoing the agent's OWN clone back is fine (console re-saving settings)
    r = client.patch("/agents/vc-guard", headers=H,
                     json={"voice": "inworld:fake-voice-1", "name": "T2"})
    assert r.status_code == 200 and r.json()["name"] == "T2"
    client.delete("/agents/vc-guard", headers=H)


def test_create_rejects_inworld_voice(cloning_on):
    r = client.post("/agents", headers=H,
                    json={"agent_id": "vc-new", "voice": "inworld:whatever"})
    assert r.status_code == 400


def test_delete_clone_reverts_to_stock(cloning_on):
    deleted = cloning_on
    _mk("vc-del")
    client.post("/agents/vc-del/voice-clone", headers=H,
                json={"audio_b64": GOOD_B64})
    r = client.delete("/agents/vc-del/voice-clone", headers=H)
    assert r.status_code == 200 and r.json()["voice"] == "kavya"
    assert client.get("/agents/vc-del", headers=H).json()["voice"] == "kavya"
    assert "fake-voice-1" in deleted
    client.delete("/agents/vc-del", headers=H)


def test_short_sample_rejected(cloning_on):
    _mk("vc-short")
    r = client.post("/agents/vc-short/voice-clone", headers=H,
                    json={"audio_b64": base64.b64encode(b"x" * 1000).decode()})
    assert r.status_code == 400
    client.delete("/agents/vc-short", headers=H)


def test_cloning_unconfigured_is_503():
    _mk("vc-off")
    r = client.post("/agents/vc-off/voice-clone", headers=H,
                    json={"audio_b64": GOOD_B64})
    assert r.status_code == 503
    client.delete("/agents/vc-off", headers=H)
