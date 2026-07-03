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


# Unicode block -> language code, for picking which translated variant set to
# serve from the last-resort net.
_BLOCK_LANG = [
    ((0x0900, 0x097F), "hi-IN"), ((0x0980, 0x09FF), "bn-IN"),
    ((0x0A00, 0x0A7F), "pa-IN"), ((0x0A80, 0x0AFF), "gu-IN"),
    ((0x0B00, 0x0B7F), "od-IN"), ((0x0B80, 0x0BFF), "ta-IN"),
    ((0x0C00, 0x0C7F), "te-IN"), ((0x0C80, 0x0CFF), "kn-IN"),
    ((0x0D00, 0x0D7F), "ml-IN"),
]


def _script_language(s: str) -> str:
    for c in s:
        if c.isalpha() and not c.isascii():
            for (lo, hi), lang in _BLOCK_LANG:
                if lo <= ord(c) <= hi:
                    return lang
    return ""


def _tokens(s: str) -> list[str]:
    out = []
    for w in s.lower().split():
        w = w.strip(_STRIP)
        if not w or w in _STOPWORDS:
            continue
        if not w.isascii():
            # Indic normalization: machine translations and STT spell the same
            # word differently — abbreviation dots ("एच.आर." vs "एचआर"),
            # zero-width joiners, anusvara/chandrabindu variants ("सेवाएं" vs
            # "सेवाएँ"). Unify before matching.
            w = (w.replace(".", "").replace("‌", "").replace("‍", "")
                  .replace("ँ", "ं"))
            out.append(w)
            # Light Indic stemming via a prefix pseudo-token: case endings are
            # suffixes ("कंपनी"/"कंपनियों", "ಸೇವೆ"/"ಸೇವೆಗಳ"), so sharing a
            # 4-char prefix is a strong stem signal. IDF keeps common prefixes
            # from dominating.
            if len(w) > 4:
                out.append("~" + w[:4])
        else:
            # Light plural/possessive stemming so "Mondays" matches "Monday",
            # "cleanings" matches "cleaning" — STT-transcribed queries are
            # noisy and exact-token matching breaks on trivial inflections.
            if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
                w = w[:-1]
            out.append(w)
    return out


class KnowledgeBase:
    """IDF retrieval over docs AND their translations.

    ``translations`` maps a language code to a list parallel to ``docs``
    (None entries are skipped — the background warm hasn't filled them yet).
    Every variant is indexed, so a Hindi query token-matches the Hindi variant
    of an English doc, and retrieval returns the variant the caller's language
    actually matched — the model gets facts already in that language."""

    def __init__(self, docs: list[str],
                 translations: dict[str, list[str | None]] | None = None):
        self.docs = list(docs)
        # entries: (text, original_doc_index, language_code | "")
        self._entries: list[tuple[str, int, str]] = [
            (d, i, "") for i, d in enumerate(self.docs)]
        # lang -> list parallel to self.docs (None where not yet translated)
        self._by_lang: dict[str, list[str | None]] = {}
        for lang, variants in (translations or {}).items():
            parallel: list[str | None] = [None] * len(self.docs)
            for i, v in enumerate(variants or []):
                if v and i < len(self.docs):
                    self._entries.append((v, i, lang))
                    parallel[i] = v
            if any(parallel):
                self._by_lang[lang] = parallel
        self._entry_tokens = [set(_tokens(t)) for t, _, _ in self._entries]
        df: dict[str, int] = {}
        for toks in self._entry_tokens:
            for t in toks:
                df[t] = df.get(t, 0) + 1
        n = len(self._entries) or 1
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
        """Top-``k`` facts whose normalized IDF overlap with the query clears
        ``min_score``. Empty list when nothing is relevant (no noise injected).
        Matching runs over every language variant; at most one variant per
        original doc is returned (the best-scoring one — normally the variant
        in the caller's own language).

        Last-resort net: if the query is non-Latin and NOTHING matched (e.g.
        the background translation warm hasn't completed yet), inject the KB
        up to a budget — in the query's language when variants exist —
        because an empty FACTS section makes the model invent answers."""
        q = set(_tokens(query))
        if not q:
            return []
        q_mass = sum(self._idf.get(t, self._default_idf) for t in q) or 1.0
        best: dict[int, tuple[float, str]] = {}      # orig doc idx -> (score, text)
        for (text, orig, _lang), toks in zip(self._entries, self._entry_tokens):
            overlap = q & toks
            if not overlap:
                continue
            score = sum(self._idf.get(t, 0.0) for t in overlap) / q_mass
            if score > best.get(orig, (0.0, ""))[0]:
                best[orig] = (score, text)
        ranked = sorted(best.items(), key=lambda x: x[1][0], reverse=True)
        hits = [text for _, (score, text) in ranked if score >= min_score][:k]
        if not hits and _mostly_non_latin(query):
            # Serve the KB in the CALLER's language, ordered by whatever weak
            # relevance signal exists (sub-threshold scores beat file order),
            # capped by budget. Grounded-but-broad beats empty-and-inventing.
            lang = _script_language(query)
            variants = self._by_lang.get(lang)
            order = [i for i, _ in ranked] + \
                    [i for i in range(len(self.docs)) if i not in best]
            out, used = [], 0
            for i in order:
                d = (variants[i] if variants and i < len(variants) and variants[i]
                     else self.docs[i])
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
