"""SonusLabs general assistant: web-search decision + registry wiring."""
from src.agent.demo_tools import _wants_web, build_assistant_registry
from src.agent.catalog import build_tools


def test_wants_web_fires_on_current_realtime():
    for q in [
        "who is the prime minister of India",
        "what's the weather in Delhi right now",
        "tell me the latest news about ISRO",
        "who won the cricket match today",
        "what is the gold rate today",
        "what's the sensex now",
        "can you look up the score",
    ]:
        assert _wants_web(q), q


def test_wants_web_fires_on_company_research_and_domains():
    # Heard live: none of these searched -> the assistant improvised "I can't research that".
    for q in [
        "do some research about a company named Koko Livo LLC",
        "tell me something about that company",
        "the company name is cocolevio.com",
        "do some indirect research about that",
        "cocolevio.com",                       # a bare domain is enough
        "tell me about cocolevio",
        "look up cocolevio for me",
        "who owns tesla",
        "find me info about zomato",
    ]:
        assert _wants_web(q), q


def test_wants_web_skips_chitchat_self_and_general_knowledge():
    for q in [
        # chitchat / emotional / casual
        "hi", "hello", "how are you", "thanks", "thank you so much",
        "okay cool", "who are you", "what's your name", "wait wait",
        "what is this bullshit", "can you laugh", "are you a robot",
        # about the assistant
        "tell me about SonusLabs", "what can you do", "what is your pricing",
        "who built you", "tell me about this receptionist", "tell me about yourself",
        # general knowledge the LLM should answer itself (no live data needed)
        "what is the capital of France", "explain how a jet engine works",
        "how tall is Mount Everest",
    ]:
        assert not _wants_web(q), q


def test_assistant_registry_has_search_prefetch_and_filler():
    reg = build_assistant_registry()
    assert any(t.name == "web_search" for t in reg.all())
    assert getattr(reg, "_prefetchers", []), "live_answer_prefetch missing"
    # filler hint returns a line for a live-info question, None otherwise
    assert reg.filler_hint("what is the weather in Delhi today")
    assert reg.filler_hint("hi there") is None
    assert reg.filler_hint("what is the capital of France") is None  # LLM knows it


def test_build_tools_carries_prefetch_and_filler():
    """The catalog merge must preserve prefetchers + the filler hint, not just
    the callable tools."""
    merged = build_tools(["assistant"])
    assert merged is not None
    assert getattr(merged, "_prefetchers", []), "prefetcher lost in merge"
    assert merged.filler_hint("what is the gold rate today")
    assert merged.filler_hint("thanks") is None
