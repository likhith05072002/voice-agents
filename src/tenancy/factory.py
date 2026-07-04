"""Build runtime objects from an ``AgentConfig``.

The single place that maps a business's declarative config to a live
``TurnEngine`` (and the eagerness profile its STT needs). Keeping this mapping in
one factory means the call path never reads global settings, and a new per-agent
knob is wired in exactly one place.
"""

from __future__ import annotations

from src.pipeline.turn_engine import TurnEngine
from src.pipeline.eagerness import resolve as resolve_eagerness, EagernessProfile
from src.agent.demo_tools import build_demo_registry
from src.agent.catalog import build_tools
from src.agent.retrieval import build_demo_kb, KnowledgeBase
from src.agent.kb_i18n import load_cached
from src.tenancy.agents import AgentConfig


def _tools_for(agent: AgentConfig):
    if not agent.enable_tools:
        return None
    if agent.tool_sets:                       # declarative per-agent tool sets
        return build_tools(agent.tool_sets)
    return build_demo_registry()              # back-compat: flag on, no sets


def _knowledge_for(agent: AgentConfig):
    if not agent.enable_rag:
        return None
    if agent.knowledge_docs:                  # inline per-business knowledge
        # Cached machine translations (warmed in the background at startup)
        # make retrieval language-agnostic — a Hindi caller token-matches the
        # Hindi variant of an English doc. Cache misses are skipped silently.
        return KnowledgeBase(agent.knowledge_docs,
                             translations=load_cached(agent.knowledge_docs))
    return build_demo_kb()                     # back-compat


def agent_eagerness(agent: AgentConfig) -> EagernessProfile:
    """Resolve the agent's eagerness preset (custom -> balanced defaults)."""
    return resolve_eagerness(
        agent.eagerness,
        min_words=2, false_timeout_s=1.2, speech_end_grace_s=0.3,
        continuation_timeout_s=0.6, high_vad_sensitivity=True,
    )


def build_engine(
    agent: AgentConfig,
    *,
    stt,
    llm,
    tts,
    send_media,
    filler=None,
    on_transcript=None,
    on_metrics=None,
    on_false_recovery=None,
    on_pause=None,
    turn_bucket=None,
    idle_reprompt_s: float = 10.0,
    idle_hangup_s: float = 30.0,
    extra_context: str = "",
    greeting_audio: bytes | None = None,
    instant_pause: bool = True,
) -> TurnEngine:
    eag = agent_eagerness(agent)
    system_prompt = agent.system_prompt
    if extra_context:                  # per-call context (e.g. outbound campaign vars)
        system_prompt = f"{system_prompt}\n\n{extra_context}"
    return TurnEngine(
        stt=stt,
        llm=llm,
        tts=tts,
        send_media=send_media,
        system_prompt=system_prompt,
        greeting_text=agent.greeting_text,
        filler=filler,
        enable_fillers=agent.enable_fillers,
        tools=_tools_for(agent),
        greeting_audio=greeting_audio,
        enable_safety=agent.enable_safety,
        min_words=eag.min_words,
        false_timeout_s=eag.false_timeout_s,
        speech_end_grace_s=eag.speech_end_grace_s,
        instant_pause=instant_pause,
        # Forced-flush endpointing splits two-part utterances at the breath;
        # hold-and-merge is what keeps those splits from becoming answered
        # half-questions (see endpointing.looks_continuable).
        enable_smart_endpointing=True,
        continuation_timeout_s=eag.continuation_timeout_s,
        enable_idle=agent.enable_idle,
        idle_reprompt_s=idle_reprompt_s,
        idle_hangup_s=idle_hangup_s,
        reprompt_text=agent.idle_reprompt_text,
        enable_language_switch=agent.enable_language_switch,
        knowledge=_knowledge_for(agent),
        on_transcript=on_transcript,
        on_metrics=on_metrics,
        on_false_recovery=on_false_recovery,
        on_pause=on_pause,
        turn_bucket=turn_bucket,
    )
