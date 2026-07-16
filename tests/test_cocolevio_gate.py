"""/cocolevio is a PRIVATE sales demo: it must 404 unless explicitly enabled
(it exposes an unauthenticated outbound-dial box — must never ship public)."""

from fastapi.testclient import TestClient

import src.main as main
from src.main import app

client = TestClient(app)


def test_cocolevio_404_by_default():
    # conftest sets no COCOLEVIO_DEMO_ENABLED -> config default False
    assert client.get("/cocolevio").status_code == 404


def test_cocolevio_serves_when_enabled(monkeypatch):
    monkeypatch.setattr(main.settings, "cocolevio_demo_enabled", True)
    r = client.get("/cocolevio")
    assert r.status_code == 200 and "ttsProvider" in r.text