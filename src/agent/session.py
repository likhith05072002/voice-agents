"""Agents, routing/handoff, and per-session configuration.

  - ``Agent``        — a named persona: its own system prompt, optional tools and
    voice. Lets one call switch between, e.g., a sales agent and a support agent.
  - ``AgentRouter``  — picks the agent for an utterance (sticky keyword routing;
    a model-based router is a drop-in later) and supports explicit handoff.
  - ``SessionConfig`` — the per-call knobs (language, voice, prompt, feature
    flags) so each call can be configured independently of process defaults.

Pure and deterministic — no LLM or network here.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Agent:
    name: str
    system_prompt: str
    tools: object | None = None          # ToolRegistry | None
    voice: str | None = None


class AgentRouter:
    """Sticky keyword router. Stays on the current agent until an utterance
    matches another agent's keywords (or an explicit ``handoff``)."""

    def __init__(self, agents: list[Agent], default: str,
                 keywords: dict[str, list[str]] | None = None):
        self._agents = {a.name: a for a in agents}
        if default not in self._agents:
            raise ValueError(f"default agent '{default}' not in agents")
        self.current = default
        self._keywords = keywords or {}

    @property
    def agent(self) -> Agent:
        return self._agents[self.current]

    def route(self, transcript: str) -> Agent:
        t = (transcript or "").lower()
        for name, kws in self._keywords.items():
            if name in self._agents and any(k in t for k in kws):
                self.current = name
                break
        return self._agents[self.current]

    def handoff(self, name: str) -> bool:
        if name in self._agents:
            self.current = name
            return True
        return False


@dataclass
class SessionConfig:
    """Per-call configuration, independent of process-wide settings."""
    language: str = "te-IN"
    voice: str = "anushka"
    system_prompt: str = ""
    greeting_text: str = ""
    enable_tools: bool = False
    enable_rag: bool = False
    metadata: dict = field(default_factory=dict)


def build_demo_router(sales_prompt: str, support_prompt: str) -> AgentRouter:
    """Demo: a sales agent (default) that hands off to support on complaint cues."""
    return AgentRouter(
        agents=[
            Agent("sales", sales_prompt),
            Agent("support", support_prompt),
        ],
        default="sales",
        keywords={
            "support": ["complaint", "repair", "broken", "return", "refund",
                        "exchange", "damaged", "problem", "issue", "fix"],
            "sales": ["buy", "price", "new", "show", "purchase", "gold rate"],
        },
    )
