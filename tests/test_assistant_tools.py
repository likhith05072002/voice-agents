"""SonusLabs general assistant: web-search decision + registry wiring."""
from src.agent.demo_tools import _wants_web, build_assistant_registry
from src.agent.catalog import build_tools


def test_wants_web_fires_on_real_questions():
    for q in [
        "what is the capital of France",
        "who is the prime minister of India",
        "explain how a jet engine works",
        "what's the weather in Delhi right now",
        "tell me the latest news about ISRO",
        "how tall is Mount Everest?",
    ]:
        assert _wants_web(q), q


def test_wants_web_skips_chitchat_and_self():
    for q in [
        "hi", "hello", "how are you", "thanks", "thank you so much",
        "okay cool", "who are you", "what's your name",
        "tell me about SonusLabs", "what can you do",
        "what is your pricing", "who built you",
    ]:
        assert not _wants_web(q), q


def test_assistant_registry_has_search_prefetch_and_filler():
    reg = build_assistant_registry()
    assert any(t.name == "web_search" for t in reg.all())
    assert getattr(reg, "_prefetchers", []), "live_answer_prefetch missing"
    # filler hint returns a line for a real question, None for chitchat
    assert reg.filler_hint("what is the capital of France")
    assert reg.filler_hint("hi there") is None


def test_build_tools_carries_prefetch_and_filler():
    """The catalog merge must preserve prefetchers + the filler hint, not just
    the callable tools."""
    merged = build_tools(["assistant"])
    assert merged is not None
    assert getattr(merged, "_prefetchers", []), "prefetcher lost in merge"
    assert merged.filler_hint("what is the tallest building")
    assert merged.filler_hint("thanks") is None
