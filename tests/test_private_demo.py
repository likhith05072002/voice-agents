"""/demo serves a PRIVATE, repo-external demo file: it must 404 unless .env
points at one (client demos expose an unauthenticated outbound-dial box —
they must never ship on the public site or in the repo)."""

from fastapi.testclient import TestClient

import src.main as main
from src.main import app

client = TestClient(app)


def test_demo_404_by_default():
    # conftest pins PRIVATE_DEMO_FILE="" -> config default: no file, no page
    assert client.get("/demo").status_code == 404


def test_demo_serves_configured_file(monkeypatch, tmp_path):
    f = tmp_path / "demo.html"
    f.write_text("<html><body>private demo ok</body></html>", encoding="utf-8")
    monkeypatch.setattr(main.settings, "private_demo_file", str(f))
    r = client.get("/demo")
    assert r.status_code == 200 and "private demo ok" in r.text
    assert r.headers.get("cache-control") == "no-store"


def test_demo_missing_file_is_404(monkeypatch):
    monkeypatch.setattr(main.settings, "private_demo_file", "private/nope.html")
    assert client.get("/demo").status_code == 404
