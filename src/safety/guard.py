"""Trust-boundary guards for the voice agent.

Two independent defenses, because the transcript is untrusted input fed straight
into the LLM prompt:

  - ``is_injection`` — flags classic prompt-injection / jailbreak phrasing in the
    caller's transcript (for logging + optional refusal). Detection only; we do
    NOT mutate what the caller said.
  - ``guard_sentence`` — the high-precision one: blocks an assistant sentence
    that leaks a verbatim span of the system prompt, replacing it with a refusal
    BEFORE it is spoken. A streamed reply is checked per sentence so a leak never
    reaches the carrier.

Voice is narrowband and transcribed, so detection is heuristic and biased toward
precision (don't refuse legitimate callers); the leak guard is the real wall.
"""

from __future__ import annotations

import re

# Brand-neutral: used by EVERY agent (was a hardcoded jewellery line that leaked
# into the general assistant — "anything about our jewellery?" on a SonusLabs call).
DEFAULT_REFUSAL = "Sorry, I can't help with that one. Is there anything else I can do for you?"

# Prompt-injection / jailbreak cues (English + a few romanized Indic). Case-
# insensitive, matched as phrases. Used for flagging — kept reasonably tight.
_INJECTION_PATTERNS = [
    r"ignore (?:all |the )?(?:previous|prior|above) (?:instructions?|prompts?)",
    r"disregard (?:all |the )?(?:previous|prior|above)",
    r"forget (?:everything|all|your instructions?)",
    r"you are now\b",
    r"(?:reveal|repeat|print|show|tell me) (?:your |the )?(?:system )?(?:prompt|instructions?)",
    r"what (?:is|are) your (?:system )?(?:prompt|instructions?)",
    r"pretend (?:to be|you are)\b",
    r"developer mode",
    r"jailbreak",
    r"system prompt",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)


def is_injection(text: str) -> bool:
    """True if the transcript looks like a prompt-injection / jailbreak attempt."""
    return bool(_INJECTION_RE.search(text or ""))


def _normalize(s: str) -> str:
    return " ".join(s.lower().split())


def leaks_system_prompt(text: str, system_prompt: str, *, min_overlap: int = 120) -> bool:
    """True if ``text`` contains a long verbatim span of the system prompt.

    A contiguous overlap of ``min_overlap`` normalized chars is a strong signal
    the model is dumping its instructions rather than answering. Threshold is
    deliberately HIGH: at 40 chars a legitimate self-description ("SonusLabs
    builds human-sounding AI voice agents…" — which is IN the prompt) tripped it
    and got refused. A real prompt-dump reproduces long contiguous spans; a
    natural answer that shares a phrase does not."""
    nt, ns = _normalize(text), _normalize(system_prompt)
    if len(ns) < min_overlap:
        return False
    for i in range(0, len(ns) - min_overlap + 1, 8):   # step to bound cost
        if ns[i:i + min_overlap] in nt:
            return True
    return False


def guard_sentence(sentence: str, system_prompt: str, *, refusal: str = DEFAULT_REFUSAL):
    """Return ``(text, blocked)``. If the sentence leaks the system prompt,
    ``text`` is the refusal and ``blocked`` is True; otherwise the sentence is
    passed through unchanged."""
    if leaks_system_prompt(sentence, system_prompt):
        return refusal, True
    return sentence, False
