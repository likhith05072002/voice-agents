"""LLM provider selection — OpenRouter took over calls on 2026-08-04.

Sarvam silently force-enabled "thinking" on sarvam-105b (its off-switch now
hangs their gateway): empty replies at voice-sized token budgets, ~14s
first-word latency at 2048. Gemini Flash via OpenRouter measured 1.0-1.6s
across en/hi/kn. These tests pin the routing rules so a stored agent config
can't quietly send the wrong model id to the wrong provider.
"""

from src.config import settings
from src.main import _build_llm


def test_auto_uses_openrouter_when_key_present(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "auto")
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key")
    c = _build_llm()
    assert c.base_url == "https://openrouter.ai/api/v1"
    assert c.model == settings.openrouter_llm_model
    assert c.api_key == "or-key"


def test_auto_falls_back_to_sarvam_without_key(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "auto")
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    c = _build_llm()
    assert c.base_url == settings.sarvam_llm_base_url
    assert c.model == settings.sarvam_llm_model


def test_sarvam_model_id_never_sent_to_openrouter(monkeypatch):
    """Agents saved before the switch carry llm_model='sarvam-105b' — sending
    that id to OpenRouter would 400 every call for exactly those agents."""
    monkeypatch.setattr(settings, "llm_provider", "auto")
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key")
    c = _build_llm(model="sarvam-105b")
    assert c.model == settings.openrouter_llm_model     # replaced, not passed


def test_openrouter_model_id_never_sent_to_sarvam(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "sarvam")
    c = _build_llm(model="google/gemini-2.5-flash")
    assert c.model == settings.sarvam_llm_model


def test_explicit_openrouter_model_is_honoured(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openrouter")
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key")
    c = _build_llm(model="openai/gpt-4o-mini")
    assert c.model == "openai/gpt-4o-mini"


def test_forced_sarvam_ignores_openrouter_key(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "sarvam")
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key")
    c = _build_llm(max_tokens=900)
    assert c.base_url == settings.sarvam_llm_base_url
    assert c.max_tokens == 900
