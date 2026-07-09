"""Prompt enhancer + role-flexible onboarding + KB file parsing.

Regression for the live misfire: 'behave like a debt recovery agent' produced
a receptionist for the WRONG business (a German music project with the same
name) — the role is now LLM-written from the owner's description, and source
priority is description > website text > web search."""

import base64

from fastapi.testclient import TestClient

from src.main import app, _chunk_doc_text
from src.onboarding import enhance_prompt, research_website

client = TestClient(app)
H = {"x-api-key": "admin-secret"}


async def test_enhance_prompt_builds_role_from_description():
    async def fake_json(messages):
        assert "debt recovery" in messages[1]["content"]
        return {"assistant_name": "Arjun",
                "role_line": "a firm but respectful debt recovery agent for Acme Finance.",
                "personality_line": "You are calm and persistent.",
                "task_section": "RECOVERY: State the due amount and agree on a payment date.",
                "boundaries_line": "Never threaten or harass; respect do-not-call requests."}
    out = await enhance_prompt(description="behave like a debt recovery agent",
                               business_name="Acme Finance", complete_json=fake_json)
    assert "debt recovery agent" in out
    assert "RECOVERY:" in out
    # the live-call guardrails survive enhancement verbatim
    assert "THIS IS A LIVE PHONE CALL" in out
    assert "SAME language" in out


async def test_enhance_prompt_rejects_empty():
    async def fake_json(messages):
        return {}
    try:
        await enhance_prompt(description="  ", complete_json=fake_json)
        assert False, "should have raised"
    except ValueError:
        pass


async def test_research_role_line_reaches_prompt(monkeypatch):
    import src.onboarding as ob

    async def fake_fetch(url):
        return "SonusLabs voice AI for businesses in India"

    async def fake_search(hint, key):
        return "SonusLabs is an ambient music project from Germany"  # the trap

    async def fake_json(messages):
        # priority instruction must be present for the model
        assert "ALWAYS wins" in messages[0]["content"]
        return {"business_name": "SonusLabs",
                "role_line": "a debt recovery agent calling on behalf of SonusLabs.",
                "greeting_text": "Hello, this is SonusLabs calling about your account.",
                "knowledge_docs": ["SonusLabs is a voice AI platform."],
                "suggested_language": "en-IN"}

    monkeypatch.setattr(ob, "_fetch_site", fake_fetch)
    monkeypatch.setattr(ob, "_search_summary", fake_search)
    draft = await research_website(url="https://sonuslabs.online",
                                   description="behave like a debt recovery agent",
                                   complete_json=fake_json)
    assert "debt recovery agent" in draft["system_prompt"]
    assert "THIS IS A LIVE PHONE CALL" in draft["system_prompt"]


def test_chunk_doc_text_paragraphs_and_caps():
    text = "\n\n".join(f"Fact number {i}: " + "x" * 120 for i in range(10))
    docs = _chunk_doc_text(text, chunk_chars=300)
    assert all(len(d) <= 300 for d in docs)
    assert "".join(docs).count("Fact number") == 10
    # oversized single paragraph gets hard-split, not dropped
    docs2 = _chunk_doc_text("y" * 5000, chunk_chars=1800)
    assert sum(len(d) for d in docs2) == 5000


def test_parse_doc_endpoint_txt_and_bad_type():
    b64 = base64.b64encode("Menu:\n\nDosa Rs. 60\n\nIdli Rs. 40".encode()).decode()
    r = client.post("/onboard/parse-doc", headers=H,
                    json={"filename": "menu.txt", "content_b64": b64})
    assert r.status_code == 200 and len(r.json()["docs"]) >= 1
    r = client.post("/onboard/parse-doc", headers=H,
                    json={"filename": "virus.exe", "content_b64": b64})
    assert r.status_code == 415
    r = client.post("/onboard/parse-doc", headers=H,
                    json={"filename": "menu.txt", "content_b64": "!!!not-b64!!!"})
    assert r.status_code in (400, 422)
