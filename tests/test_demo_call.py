"""Public /demo/call-me: every click dials a real phone on our carrier
account, so the guards ARE the feature. Off by default; allowlisted country
prefixes only; throttled per IP, per destination number, and globally."""

import pytest
from fastapi.testclient import TestClient

import src.main as main
from src.main import app

client = TestClient(app)


@pytest.fixture()
def demo_on(monkeypatch):
    """Enable the endpoint with a stubbed carrier (no real calls placed)."""
    placed: list[dict] = []

    async def fake_place(*, to, agent_id, from_number, context):
        placed.append({"to": to, "agent_id": agent_id, "context": context})
        return "ccid-test"

    monkeypatch.setattr(main.settings, "demo_call_enabled", True)
    monkeypatch.setattr(main.settings, "telnyx_connection_id", "conn-test")
    monkeypatch.setattr(main, "_place_outbound", fake_place)
    main._demo_call_ip.clear()
    main._demo_call_num.clear()
    main._demo_call_all.clear()
    return placed


def test_disabled_by_default():
    # No monkeypatch: config default is off -> never dials.
    assert client.post("/demo/call-me", json={"phone": "+919999999999"}).status_code == 503


def test_places_call_and_flags_it_as_demo(demo_on):
    r = client.post("/demo/call-me", json={"phone": "+91 98765 43210"})
    assert r.status_code == 200 and r.json()["status"] == "calling"
    assert demo_on[0]["to"] == "+919876543210"          # normalized to E.164
    # the flag /media-stream reads to apply the 3-minute cap
    assert demo_on[0]["context"]["demo_call"] is True


def test_rejects_disallowed_country(demo_on):
    # +44 is not in the default allowlist (+91,+1): premium-rate/foreign
    # ranges are the toll-fraud payout path.
    r = client.post("/demo/call-me", json={"phone": "+447700900000"})
    assert r.status_code == 400 and not demo_on


def test_rejects_malformed_number(demo_on):
    assert client.post("/demo/call-me", json={"phone": "12345"}).status_code == 400
    assert client.post("/demo/call-me", json={"phone": "not a number"}).status_code == 400
    assert not demo_on


def test_same_ip_is_cooled_down(demo_on):
    assert client.post("/demo/call-me", json={"phone": "+919000000001"}).status_code == 200
    # different number, same IP -> refused by the IP cooldown
    r = client.post("/demo/call-me", json={"phone": "+919000000002"})
    assert r.status_code == 429 and len(demo_on) == 1


def test_same_number_is_cooled_down_across_ips(demo_on, monkeypatch):
    monkeypatch.setattr(main.settings, "demo_call_ip_cooldown_s", 0)
    monkeypatch.setattr(main.settings, "demo_call_per_ip_daily", 99)
    assert client.post("/demo/call-me", json={"phone": "+919000000003"}).status_code == 200
    r = client.post("/demo/call-me", json={"phone": "+91 90000 00003"})
    assert r.status_code == 429 and len(demo_on) == 1


def test_global_hourly_cap_is_the_spend_ceiling(demo_on, monkeypatch):
    monkeypatch.setattr(main.settings, "demo_call_ip_cooldown_s", 0)
    monkeypatch.setattr(main.settings, "demo_call_number_cooldown_s", 0)
    monkeypatch.setattr(main.settings, "demo_call_per_ip_daily", 99)
    monkeypatch.setattr(main.settings, "demo_call_per_number_daily", 99)
    monkeypatch.setattr(main.settings, "demo_call_global_hourly", 2)
    for i in range(2):
        assert client.post("/demo/call-me",
                           json={"phone": f"+91900000010{i}"}).status_code == 200
    r = client.post("/demo/call-me", json={"phone": "+919000000199"})
    assert r.status_code == 429 and len(demo_on) == 2


def test_carrier_failure_does_not_burn_the_allowance(demo_on, monkeypatch):
    async def boom(**kwargs):
        raise RuntimeError("carrier down")

    monkeypatch.setattr(main, "_place_outbound", boom)
    assert client.post("/demo/call-me", json={"phone": "+919000000004"}).status_code == 502
    # the failed attempt was not counted, so a retry is allowed
    monkeypatch.setattr(main, "_place_outbound", lambda **kw: _ok(demo_on, kw))
    r = client.post("/demo/call-me", json={"phone": "+919000000004"})
    assert r.status_code == 200


async def _ok(placed, kw):
    placed.append(kw)
    return "ccid-retry"
