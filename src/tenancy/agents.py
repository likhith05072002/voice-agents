"""Per-business agent configuration — the multi-tenant unit of the platform.

The process no longer serves a single hardcoded persona. Each business is an
``AgentConfig`` (persona, voice, language, behavior, feature flags, and the
phone numbers that route to it). A call resolves to an agent, and the engine is
built from that agent's config — so one deployment serves hundreds of businesses.

This mirrors how production voice platforms (Retell/Vapi/Bland) model "agents":
an addressable config object keyed by id and phone number, loaded per call.

``AgentConfig`` is a plain dataclass so it round-trips trivially to/from JSON
(file store now, database later) without touching call-path code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict


def normalize_number(number: str) -> str:
    """Digits-only form of a phone number for robust DID matching
    (`+91 98765-43210` -> `919876543210`)."""
    return re.sub(r"\D", "", number or "")


@dataclass
class AgentConfig:
    # Default "" so from_dict() tolerates a body without an id — the create
    # endpoint generates the real, globally-unique id server-side (the API
    # contract: clients don't pick ids). Legacy create still rejects empty.
    agent_id: str = ""
    name: str = ""

    # Owning workspace (accounts mode). Server-controlled: set at create from
    # the authenticated workspace, never from a client PATCH body. "" = platform
    # agent (default fallback, public landing demo). from_dict() ignores unknown
    # keys, so pre-accounts stored blobs load fine with the default.
    workspace_id: str = ""

    # Voice & language
    language: str = "te-IN"
    voice: str = "anushka"
    voice_pace: float = 1.0            # 0.5-2.0; ~0.95 = calmer, more human
    enable_language_switch: bool = True
    enable_human_expression: bool = True   # natural hums/backchannels/pauses in text

    # Persona
    system_prompt: str = ""
    greeting_text: str = ""
    idle_reprompt_text: str = ""

    # Turn-taking behavior
    eagerness: str = "balanced"

    # LLM overrides (empty = deployment default). Lets one business run a
    # bigger/smaller model or an explicit reasoning level than the platform default.
    llm_model: str = ""
    llm_reasoning_effort: str = ""   # "" = default (off); "low"/"medium"/"high"

    # Feature flags (per business)
    enable_tools: bool = False
    enable_rag: bool = False
    enable_safety: bool = True
    enable_idle: bool = True
    enable_fillers: bool = False
    enable_input_dsp: bool = True
    enable_echo_cancellation: bool = False
    # Spectral noise suppression (removes steady background noise from under
    # speech, +8dB SNR measured). Opt-in until validated against live STT.
    enable_noise_suppression: bool = False

    # Per-agent tools & knowledge (declarative).
    #   tool_sets     -> names looked up in the tool catalog (e.g. ["jewellery"])
    #   knowledge_docs-> inline RAG snippets specific to this business
    tool_sets: list[str] = field(default_factory=list)
    knowledge_docs: list[str] = field(default_factory=list)

    # Limits (per-agent override; 0 = inherit / disabled)
    max_turns_per_min: int = 0

    # Event webhook: POST the call record (+summary) here on completion.
    webhook_url: str = ""
    webhook_secret: str = ""

    # Human handoff: named transfer destinations the LLM may route to
    # (e.g. {"owner": "+91...", "billing": "+1..."}). Empty -> no transfer tool.
    transfer_numbers: dict = field(default_factory=dict)

    # Telephony routing: DIDs that ring this agent.
    phone_numbers: list[str] = field(default_factory=list)

    metadata: dict = field(default_factory=dict)

    def normalized_numbers(self) -> list[str]:
        return [normalize_number(n) for n in self.phone_numbers if n]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AgentConfig":
        # Ignore unknown keys so adding fields never breaks older stored configs.
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


def agent_from_settings(settings, agent_id: str = "default") -> AgentConfig:
    """Build the default agent from the global Settings — preserves single-tenant
    behavior so existing deployments keep working unchanged."""
    return AgentConfig(
        agent_id=agent_id,
        name="default",
        language=settings.default_language,
        voice=settings.sarvam_tts_voice,
        enable_language_switch=settings.enable_language_switch,
        system_prompt=settings.system_prompt,
        greeting_text=settings.greeting_text,
        idle_reprompt_text=settings.idle_reprompt_text,
        eagerness=settings.eagerness,
        enable_tools=settings.enable_tools,
        enable_rag=settings.enable_rag,
        enable_safety=settings.enable_safety,
        enable_idle=settings.enable_idle,
        enable_fillers=settings.enable_fillers,
        enable_input_dsp=settings.enable_input_dsp,
        enable_echo_cancellation=settings.enable_echo_cancellation,
        enable_noise_suppression=settings.enable_noise_suppression,
        max_turns_per_min=settings.max_turns_per_min,
    )
