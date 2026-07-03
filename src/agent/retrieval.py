"""Lightweight retrieval for grounding the agent in shop knowledge (RAG).

No vector DB or embeddings service — for an FAQ/knowledge set of dozens of short
facts, IDF-weighted token overlap retrieves the right snippet with zero network
latency and full determinism. The engine injects the top matches into the system
context so the model answers from facts instead of hallucinating.

Tokenization is Unicode-word based, so it works for romanized and native Indic
scripts alike.
"""

from __future__ import annotations

import math
import string

# Strip edge punctuation (ASCII + Indic danda) but split only on whitespace, so
# Indic combining marks/matras stay attached to their consonant. A \w+ regex
# would split "బంగారం" into pieces because matras aren't \w.
_STRIP = string.punctuation + "।॥…"

# Common English function words carry no retrieval signal and otherwise let a
# query match a doc purely on a shared "the"/"on". Dropped from both indexing
# and queries. (Native-script tokens are unaffected.)
_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "am", "to", "of",
    "on", "in", "at", "for", "and", "or", "but", "do", "does", "did", "you",
    "your", "my", "me", "i", "we", "it", "this", "that", "what", "how", "can",
    "could", "would", "please", "tell", "about", "with", "have", "has",
})


def _mostly_non_latin(s: str) -> bool:
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return False
    return sum(not c.isascii() for c in letters) / len(letters) > 0.5


def _tokens(s: str) -> list[str]:
    out = []
    for w in s.lower().split():
        w = w.strip(_STRIP)
        if not w or w in _STOPWORDS:
            continue
        # Light plural/possessive stemming so "Mondays" matches "Monday",
        # "cleanings" matches "cleaning" — STT-transcribed queries are noisy
        # and exact-token matching breaks on trivial inflections.
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


class KnowledgeBase:
    def __init__(self, docs: list[str]):
        self.docs = list(docs)
        self._doc_tokens = [set(_tokens(d)) for d in self.docs]
        df: dict[str, int] = {}
        for toks in self._doc_tokens:
            for t in toks:
                df[t] = df.get(t, 0) + 1
        n = len(self.docs) or 1
        # smoothed IDF: rare words weigh more
        self._idf = {t: math.log(1 + n / (1 + c)) for t, c in df.items()}
        # Out-of-vocabulary query words are maximally specific — give them the
        # highest IDF so a query about unknown things can't score high purely on
        # a shared stopword ("the", "on").
        self._default_idf = math.log(1 + n)

    # Cross-script fallback budget: how much of the KB to inject verbatim when
    # token matching is structurally impossible (see retrieve()).
    _FALLBACK_CHARS = 1500

    def retrieve(self, query: str, *, k: int = 2, min_score: float = 0.15) -> list[str]:
        """Top-``k`` docs whose normalized IDF overlap with the query clears
        ``min_score``. Empty list when nothing is relevant (no noise injected).

        Script-mismatch fallback: a Hindi/Kannada query can NEVER token-match an
        English KB (zero overlap by construction), and an empty FACTS section
        makes the model invent answers (live: denied CocolevioHR existed when
        asked in Hindi). When the query is mostly non-Latin and nothing matched,
        inject the whole KB up to a budget — receptionist KBs are dozens of
        short facts, so this is cheap and always grounded."""
        q = set(_tokens(query))
        if not q:
            return []
        q_mass = sum(self._idf.get(t, self._default_idf) for t in q) or 1.0
        scored: list[tuple[float, str]] = []
        for doc, toks in zip(self.docs, self._doc_tokens):
            overlap = q & toks
            if not overlap:
                continue
            score = sum(self._idf.get(t, 0.0) for t in overlap) / q_mass
            scored.append((score, doc))
        scored.sort(key=lambda x: x[0], reverse=True)
        hits = [doc for score, doc in scored if score >= min_score][:k]
        if not hits and _mostly_non_latin(query):
            out, used = [], 0
            for d in self.docs:
                if used + len(d) > self._FALLBACK_CHARS:
                    break
                out.append(d)
                used += len(d)
            return out
        return hits


def build_demo_kb() -> KnowledgeBase:
    """Sample shop knowledge for the jewellery agent."""
    return KnowledgeBase([
        "Hallmarking: all our gold jewellery is BIS hallmarked and certified.",
        "Old gold exchange: we accept old gold at the day's market rate minus a small refining charge.",
        "Making charges: 8 to 15 percent depending on the design complexity.",
        "Return policy: exchange within 15 days with the original bill; buyback at the prevailing rate.",
        "Diamond jewellery comes with an IGI or GIA certificate of authenticity.",
        "We offer a monthly gold savings scheme: pay 11 months, we add the 12th.",
        "Shop address: Banjara Hills, Hyderabad. Open 10 AM to 9 PM daily.",
    ])
