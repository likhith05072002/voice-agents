"""Output content moderation by category.

Complements ``guard.py`` (which blocks system-prompt leaks). This flags the
*spoken* reply for content categories and tells the engine which are hard blocks:

  - ``profanity`` — BLOCKING (replace the sentence with a refusal).
  - ``pii``       — flagged only (the agent emitting a phone/email/card number is
    worth logging, but legit info like a price/quantity must not be blocked, so
    we don't hard-block on it).

Deterministic and high-precision: better to miss a borderline case than to gag a
legitimate answer mid-call.
"""

from __future__ import annotations

import re

# Small, unambiguous profanity set (extend per deployment policy).
PROFANITY: frozenset[str] = frozenset({
    "fuck", "shit", "bitch", "bastard", "asshole", "dick", "cunt", "slut",
})

_PII_PATTERNS = {
    "card": re.compile(r"\b(?:\d[ -]?){15,16}\b"),
    "phone": re.compile(r"\b(?:\+?91[- ]?)?[6-9]\d{9}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "aadhaar": re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),
}

BLOCKING: frozenset[str] = frozenset({"profanity"})

_WORDS = re.compile(r"[a-z']+")


def moderate(text: str) -> set[str]:
    """Return the set of content categories present in ``text``."""
    cats: set[str] = set()
    words = set(_WORDS.findall(text.lower()))
    if words & PROFANITY:
        cats.add("profanity")
    for pat in _PII_PATTERNS.values():
        if pat.search(text):
            cats.add("pii")
            break
    return cats


def is_blocked(categories: set[str]) -> bool:
    """True if any category is a hard block (engine should refuse)."""
    return bool(categories & BLOCKING)
