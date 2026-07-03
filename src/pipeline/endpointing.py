"""Semantic endpointing — has the caller finished, or will they keep talking?

Cascaded STT (Sarvam) finalizes a transcript on trailing silence. A caller who
pauses mid-thought ("I want to know about… the gold rate today") gets split into
two finals, and firing a turn on the first fragment makes the agent answer half a
question. This module flags finals that clearly look *unfinished* so the engine
can wait a short beat for the continuation and merge them.

It is intentionally rule-based and multilingual, matching the project's
``barge_in.py`` guard stack. A neural turn-detector (LiveKit/Deepgram-style) is
the natural upgrade once streaming partial transcripts + prosody are available;
this module is the deterministic, zero-latency floor.

Bias: when in doubt, treat the utterance as COMPLETE. A false "incomplete" adds
the continuation timeout to latency, so only strong continuation cues (a trailing
conjunction/preposition, a dangling comma, an ellipsis) flag a wait.
"""

from __future__ import annotations

import string

# Sentence-final punctuation across Latin + Indic scripts -> strong "complete".
_FINAL_PUNCT = ".?!।॥"

# Trailing tokens that almost never end a finished utterance. Kept deliberately
# tight (clear conjunctions / prepositions / articles) to avoid false waits.
CONTINUATION_WORDS: frozenset[str] = frozenset({
    # English
    "and", "or", "but", "so", "because", "to", "for", "with", "of", "the", "a",
    "an", "in", "on", "at", "is", "are", "my", "your", "i", "we", "that", "if",
    "when", "about", "want", "need", "like",
    # Hindi (romanized + native)
    "aur", "ya", "lekin", "kyunki", "ki", "ka", "ke", "ko", "se", "mein", "mujhe",
    "और", "या", "लेकिन", "क्योंकि", "की", "का", "के", "को", "से", "में", "मुझे",
    # Telugu
    "mariyu", "kani", "naku", "nenu", "gurinchi",
    "మరియు", "కానీ", "నాకు", "నేను", "గురించి",
    # Kannada
    "mattu", "adare", "nanage", "bagge",
    "ಮತ್ತು", "ಆದರೆ", "ನನಗೆ", "ಬಗ್ಗೆ",
    # Tamil
    "matrum", "aanal", "enakku", "patri",
    "மற்றும்", "ஆனால்", "எனக்கு", "பற்றி",
})


def _last_word(text: str) -> str:
    # Split on whitespace only (NOT on every non-alnum char) so Indic combining
    # marks / matras stay attached to their consonant, then trim edge ASCII
    # punctuation. Splitting on isalnum() would strip the vowel sign off e.g.
    # "की" -> "क" and break the match.
    parts = text.lower().split()
    if not parts:
        return ""
    return parts[-1].strip(string.punctuation)


def looks_incomplete(text: str) -> bool:
    """True if ``text`` looks like an unfinished utterance (wait for more)."""
    stripped = text.strip()
    if not stripped:
        return False  # nothing to wait on

    # Dangling continuation FIRST: a trailing comma or ellipsis means "more
    # coming" (and an ellipsis ends in '.', so it must be checked before the
    # sentence-final-punctuation rule below).
    if stripped.endswith((",", "…")) or stripped.endswith("..."):
        return True

    # Strong completion: ends on sentence-final punctuation.
    if stripped[-1] in _FINAL_PUNCT:
        return False

    # Trailing conjunction / preposition / article -> unfinished.
    return _last_word(stripped) in CONTINUATION_WORDS
