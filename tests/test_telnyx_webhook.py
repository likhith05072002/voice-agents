"""The /webhook/telnyx handler end-to-end (fake carrier, no network).

Regression for a prod outage: a structlog kwarg named `event` collided with
structlog's positional message parameter and TypeError'd on EVERY webhook —
so calls placed fine but nothing (answer/streaming) ever happened. These
tests drive real webhook bodies through the HTTP handler, so any exception
in the handler path fails loudly here instead of in production.
"""

import pytest
from fastapi.testclient import TestClient

import src.main as main
from src.main import app

client = TestClient(app)


class _FakeTelnyx:
    def __init__(self):
        self.answered: list[str] = []
        self.streamed: list[tuple[str, str]] = []
        self.hungup: list[str] = []

    async def answer(self, ccid):
        self.answered.append(ccid)

    async def streaming_start(self, ccid, *, stream_url, track="inbound_track",
                              codec="PCMU"):
        self.streamed.append((ccid, stream_url))

    async def hangup(self, ccid):
        self.hungup.append(ccid)


@pytest.fixture()
def carrier(monkeypatch):
    fake = _FakeTelnyx()
    monkeypatch.setattr(main, "_telnyx", fake)
    monkeypatch.setattr(main.settings, "public_url", "https://unit.test")
    return fake


def _event(event_type: str, **payload) -> dict:
    return {"data": {"event_type": event_type,
                     "payload": {"call_control_id": "cc-webhook-test", **payload}}}


def test_inbound_initiated_answers_the_call(carrier):
    r = client.post("/webhook/telnyx",
                    json=_event("call.initiated", direction="incoming",
                                to="+15550001111", **{"from": "+15552223333"}))
    assert r.status_code == 200
    assert carrier.answered == ["cc-webhook-test"]


def test_answered_starts_media_stream(carrier):
    r = client.post("/webhook/telnyx", json=_event("call.answered"))
    assert r.status_code == 200
    assert carrier.streamed == [("cc-webhook-test", "wss://unit.test/media-stream")]


def test_media_start_failure_hangs_up_instead_of_silence(carrier, monkeypatch):
    async def boom(ccid, *, stream_url, **kw):
        raise RuntimeError("streaming_start failed: 422")

    monkeypatch.setattr(carrier, "streaming_start", boom)
    r = client.post("/webhook/telnyx", json=_event("call.answered"))
    assert r.status_code == 200                      # never 500 back to Telnyx
    assert carrier.hungup == ["cc-webhook-test"]     # caller isn't left in silence


def test_every_lifecycle_event_returns_200(carrier):
    """The logging path runs for every event type — the exact line that
    TypeError'd in prod. Any handler exception turns this red."""
    for et in ("call.initiated", "call.ringing", "call.answered", "call.hangup",
               "call.machine.detection.ended", "streaming.started"):
        body = _event(et, direction="outgoing", hangup_cause="normal_clearing",
                      hangup_source="callee")
        assert client.post("/webhook/telnyx", json=body).status_code == 200
