"""Tests for the AI voice tester (scenario, runner verification, endpoints)."""

import json

from src.testing.scenario import Scenario, Step, load_scenario, list_scenarios
from src.testing.runner import TestRun
from src.persistence.records import CallRecord, Turn


# ─── scenario parsing ───

def test_scenario_from_dict_and_barge_step():
    s = Scenario.from_dict({
        "name": "x", "language": "te-IN", "caller_voice": "karun",
        "steps": [
            {"say": "q1", "expect_keywords": ["a"], "max_wait_s": 5},
            {"barge_in_during_answer": "q2", "trigger_after_s": 1.5},
        ],
    })
    assert s.steps[0].say == "q1" and s.steps[0].barge_in is False
    assert s.steps[1].barge_in is True and s.steps[1].trigger_after_s == 1.5
    assert s.steps[1].say == "q2"


def test_load_and_list_scenarios(tmp_path):
    p = tmp_path / "demo.json"
    p.write_text(json.dumps({"name": "demo", "steps": [{"say": "hi"}]}), encoding="utf-8")
    s = load_scenario(str(p))
    assert s.name == "demo" and len(s.steps) == 1
    assert list_scenarios(str(tmp_path)) == ["demo"]
    assert list_scenarios(str(tmp_path / "missing")) == []


def test_repo_scenarios_are_valid():
    for name in list_scenarios("scenarios"):
        s = load_scenario(f"scenarios/{name}.json")
        assert s.name and s.steps


# ─── runner verification (transcript -> per-step answers + checks) ───

def _run_with_record(record):
    scenario = Scenario.from_dict({
        "name": "v", "steps": [
            {"say": "what is the gold price", "expect_keywords": ["7150"]},
            {"say": "shop timings", "expect_keywords": ["10"]},
        ]})

    async def get_record(call_id, after_ts=0.0):
        return record

    return TestRun(scenario, ws_url="ws://x", api_key="k", get_record=get_record)


async def test_verify_matches_answers_and_checks():
    record = CallRecord(call_id="c", agent_id="a", turns=[
        Turn("user", "what is the gold price", 0.0),
        Turn("assistant", "22 carat gold is 7150 rupees per gram.", 1.0),
        Turn("user", "shop timings", 2.0),
        Turn("assistant", "We are open until 9 PM.", 3.0),   # missing "10" -> fail
    ])
    run = _run_with_record(record)
    await run._verify("c")
    assert run.steps[0]["check"] == "pass"
    assert "7150" in run.steps[0]["answer"]
    assert run.steps[1]["check"] == "fail"


async def test_verify_handles_missing_record():
    run = _run_with_record(None)
    await run._verify("c")                     # no crash, checks stay "none"
    assert all(s["check"] == "none" for s in run.steps)


def test_status_shape():
    run = _run_with_record(None)
    st = run.status()
    assert st["state"] == "pending"
    assert len(st["steps"]) == 2
    assert st["steps"][0]["question"] == "what is the gold price"


# ─── endpoints ───

from fastapi.testclient import TestClient  # noqa: E402
from src.main import app  # noqa: E402

client = TestClient(app)


def test_dashboard_serves():
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "Start Test" in r.text


def test_scenarios_endpoint_lists_repo_scenarios():
    r = client.get("/test/scenarios")
    assert "jewellery-basic" in r.json()["scenarios"]


def test_start_unknown_scenario_404():
    r = client.post("/test/start", json={"scenario": "nope"})
    assert r.status_code == 404


def test_status_unknown_test_404():
    assert client.get("/test/status/zzz").status_code == 404
    assert client.get("/test/audio/zzz").status_code == 404
