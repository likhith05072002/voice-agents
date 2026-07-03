"""Test scenarios — the script the AI voice tester follows.

A scenario is a list of steps. A ``say`` step asks a question, measures the
perceived latency of the reply, and (optionally) checks the agent's answer for
expected keywords. A ``barge_in`` step deliberately talks over the agent's
current answer and measures how fast the agent goes silent.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass
class Step:
    say: str = ""
    expect_keywords: list[str] = field(default_factory=list)
    max_wait_s: float = 12.0
    await_full_answer: bool = True     # False -> next step fires while answer plays
    barge_in: bool = False             # speak OVER the current answer
    trigger_after_s: float = 0.8       # barge-in: how far into the answer
    language: str = ""                 # per-step caller language ("" = scenario's)
    voice: str = ""                    # per-step caller voice ("" = scenario's)
    expect_language: str = ""          # verify the ANSWER's script, e.g. "kn-IN"

    @classmethod
    def from_dict(cls, d: dict) -> "Step":
        if "barge_in_during_answer" in d:
            return cls(say=d["barge_in_during_answer"], barge_in=True,
                       trigger_after_s=d.get("trigger_after_s", 0.8),
                       expect_keywords=d.get("expect_keywords", []),
                       max_wait_s=d.get("max_wait_s", 12.0))
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Scenario:
    name: str
    language: str = "en-IN"
    caller_voice: str = "abhilash"
    main_agent_id: str = ""            # which business persona answers this test
    steps: list[Step] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Scenario":
        return cls(
            name=d["name"],
            language=d.get("language", "en-IN"),
            caller_voice=d.get("caller_voice", "abhilash"),
            main_agent_id=d.get("main_agent_id", ""),
            steps=[Step.from_dict(s) for s in d.get("steps", [])],
        )


def load_scenario(path: str) -> Scenario:
    with open(path, encoding="utf-8") as f:
        return Scenario.from_dict(json.load(f))


def list_scenarios(directory: str) -> list[str]:
    if not os.path.isdir(directory):
        return []
    return sorted(f[:-5] for f in os.listdir(directory) if f.endswith(".json"))
