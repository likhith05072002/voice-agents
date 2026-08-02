"""The operator's call log: every call, its number, and its transcript.

Two things are guarded here:
  1. /admin/calls/{id} hands the UI a real `turns` ARRAY. It is stored as JSON
     text, and shipping the raw string made the transcript panel render one
     unreadable blob.
  2. Browser/widget calls are recorded at all. For a long time only the phone
     path built a CallRecorder, so web conversations vanished on hangup and
     never appeared in the admin panel.
"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from src.accounts import admin_routes
from src.main import app
from src.persistence.records import CallRecorder

client = TestClient(app)


class _FakePool:
    def __init__(self, row):
        self._row = row

    async def fetchrow(self, *_a, **_k):
        return self._row


@pytest.fixture
def as_admin(monkeypatch):
    """Admin session without a Postgres/OAuth round-trip."""
    async def _user(_request):
        return {"id": "u1", "email": "ops@sonuslabs.online"}
    monkeypatch.setattr(admin_routes, "current_user", _user)
    monkeypatch.setattr(admin_routes, "is_admin", lambda _u: True)


def _row(**over):
    d = {
        "call_id": "web-abc123", "agent_id": "sonuslabs",
        "from_number": "127.0.0.1", "to_number": "web",
        "started_at": 1.0, "duration_s": 17.0, "turn_count": 3,
        "turns": json.dumps([{"role": "assistant", "text": "Hi!"},
                             {"role": "user", "text": "Your hours?"}]),
        "workspace_id": uuid.uuid4(),      # NOT JSON-serialisable as-is
    }
    d.update(over)
    return d


def test_transcript_comes_back_as_an_array(monkeypatch, as_admin):
    monkeypatch.setattr(admin_routes, "pool", lambda: _FakePool(_row()))
    body = client.get("/admin/calls/web-abc123").json()

    assert isinstance(body["turns"], list), "UI must not have to JSON.parse a blob"
    assert [t["role"] for t in body["turns"]] == ["assistant", "user"]
    assert body["turns"][1]["text"] == "Your hours?"
    # the far end is what the operator identifies a call by
    assert body["from_number"] == "127.0.0.1" and body["to_number"] == "web"
    # uuid/datetime columns must not blow up serialisation
    assert isinstance(body["workspace_id"], str)


@pytest.mark.parametrize("stored", [None, "", "{not json"])
def test_missing_or_corrupt_transcript_is_an_empty_list(monkeypatch, as_admin, stored):
    """An old row with no turns must render "no transcript", not a 500."""
    monkeypatch.setattr(admin_routes, "pool", lambda: _FakePool(_row(turns=stored)))
    r = client.get("/admin/calls/web-abc123")
    assert r.status_code == 200
    assert r.json()["turns"] == []


def test_unknown_call_is_404(monkeypatch, as_admin):
    monkeypatch.setattr(admin_routes, "pool", lambda: _FakePool(None))
    assert client.get("/admin/calls/nope").status_code == 404


def test_non_admin_cannot_read_transcripts(monkeypatch):
    """Transcripts are the most sensitive thing we store — 404, not 403, so the
    admin surface isn't even discoverable."""
    async def _user(_request):
        return {"id": "u2", "email": "someone@example.com"}
    monkeypatch.setattr(admin_routes, "current_user", _user)
    monkeypatch.setattr(admin_routes, "is_admin", lambda _u: False)
    called = []
    monkeypatch.setattr(admin_routes, "pool",
                        lambda: called.append(1) or _FakePool(_row()))

    assert client.get("/admin/calls/web-abc123").status_code == 404
    assert not called, "must reject before touching the database"


def test_web_calls_are_recorded_with_a_source_and_channel():
    """A browser call has no phone number; it still has to be identifiable."""
    rec = CallRecorder(call_id="web-abc123", agent_id="sonuslabs",
                       from_number="203.0.113.9", to_number="web",
                       started_at=0.0, clock=iter([0.0, 1.0, 2.0, 3.0]).__next__)
    rec.record.metadata["channel"] = "web"
    rec.transcript("assistant", "Hi!")
    rec.transcript("user", "Your hours?")
    record = rec.finalize(ended_at=17.0)

    assert record.from_number == "203.0.113.9" and record.to_number == "web"
    assert record.metadata["channel"] == "web"
    assert [t.text for t in record.turns] == ["Hi!", "Your hours?"]
