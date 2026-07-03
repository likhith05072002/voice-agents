"""Tests for the /outbound endpoint guards (auth + validation, no network)."""

import os

# Configure the app before importing it (settings are read at import).
os.environ.setdefault("SARVAM_API_KEY", "test")
os.environ.setdefault("ENABLE_PERSISTENCE", "false")   # no calls.db side-effect
os.environ.setdefault("OUTBOUND_API_KEY", "secret")
os.environ.setdefault("TELNYX_CONNECTION_ID", "")       # unset -> 400 after auth

from fastapi.testclient import TestClient  # noqa: E402

from src.main import app  # noqa: E402

client = TestClient(app)


def test_outbound_rejects_without_api_key():
    r = client.post("/outbound", json={"to": "+15551112222"})
    assert r.status_code == 401


def test_outbound_rejects_wrong_api_key():
    r = client.post("/outbound", json={"to": "+15551112222"}, headers={"x-api-key": "nope"})
    assert r.status_code == 401


def test_outbound_authed_but_unconfigured_connection_id():
    r = client.post("/outbound", json={"to": "+15551112222"},
                    headers={"x-api-key": "secret"})
    assert r.status_code == 400          # passed auth, fails on missing connection id


def test_health_reports_agents():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["agents"] >= 1       # at least the default agent


# ─── batch endpoints ───

def test_batch_requires_auth():
    r = client.post("/outbound/batch", json={"calls": [{"to": "+1"}]})
    assert r.status_code == 401


def test_batch_rejects_empty_calls():
    r = client.post("/outbound/batch", json={"calls": []},
                    headers={"x-api-key": "secret"})
    # auth passes; fails on connection id (unset in tests) or empty list — both 400
    assert r.status_code == 400


def test_batch_status_unknown_id_404():
    r = client.get("/outbound/batch/nope", headers={"x-api-key": "secret"})
    assert r.status_code == 404
