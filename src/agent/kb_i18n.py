"""Multilingual knowledge indexing — translate docs ONCE, retrieve in any language.

The platform serves many businesses; each declares knowledge_docs in whatever
language it likes (usually English). Callers ask in Hindi/Kannada/Tamil/....
Token retrieval across scripts is structurally impossible, so every doc is
machine-translated into the platform's caller languages ONCE (Sarvam translate,
disk-cached by content hash — a doc is never translated twice, and restarts or
config edits only translate what changed). The KnowledgeBase then indexes all
variants, so a Hindi query token-matches Hindi text, and the model receives
facts already in the caller's language.

Nothing here runs on the call path: translations are warmed in the background
at startup / agent-update and read synchronously from cache when an engine is
built.
"""

from __future__ import annotations

import asyncio
import hashlib
import os

import structlog

logger = structlog.get_logger()

CACHE_DIR = os.path.join("data", "kb_cache")

# Caller languages every business's KB is indexed for. Env-overridable so a
# deployment serving other regions can widen/narrow without a code change.
DEFAULT_LANGS = [
    s.strip() for s in os.environ.get(
        "KB_LANGUAGES", "hi-IN,kn-IN,te-IN,ta-IN").split(",") if s.strip()
]


def _key(text: str, lang: str) -> str:
    return hashlib.sha1(f"{text}|{lang}".encode()).hexdigest()


def _path(text: str, lang: str) -> str:
    return os.path.join(CACHE_DIR, f"{_key(text, lang)}.txt")


def load_cached(docs: list[str], langs: list[str] | None = None) -> dict[str, list[str | None]]:
    """Synchronously read whatever translations are already cached.
    Missing entries come back as None (retrieval simply skips them until the
    background warm fills the cache)."""
    langs = langs if langs is not None else DEFAULT_LANGS
    out: dict[str, list[str | None]] = {}
    for lang in langs:
        variants: list[str | None] = []
        for d in docs:
            p = _path(d, lang)
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    variants.append(f.read())
            else:
                variants.append(None)
        out[lang] = variants
    return out


async def _translate(client, text: str, lang: str, api_key: str) -> str:
    resp = await client.post(
        "https://api.sarvam.ai/translate",
        headers={"api-subscription-key": api_key},
        json={"input": text, "source_language_code": "auto",
              "target_language_code": lang})
    resp.raise_for_status()
    return resp.json()["translated_text"]


async def warm(docs: list[str], langs: list[str] | None = None, *,
               api_key: str) -> int:
    """Translate any (doc, lang) pairs not yet cached. Returns how many were
    newly translated. Safe to call repeatedly; parallel with a small cap."""
    import httpx

    langs = langs if langs is not None else DEFAULT_LANGS
    os.makedirs(CACHE_DIR, exist_ok=True)
    todo = [(d, lang) for lang in langs for d in docs
            if not os.path.exists(_path(d, lang))]
    if not todo:
        return 0
    sem = asyncio.Semaphore(4)
    done = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        async def one(d: str, lang: str):
            nonlocal done
            async with sem:
                try:
                    t = await _translate(client, d, lang, api_key)
                except Exception as e:  # noqa: BLE001 — a miss just stays a miss
                    logger.warning("kb_i18n.translate_failed", lang=lang, error=str(e))
                    return
                with open(_path(d, lang), "w", encoding="utf-8") as f:
                    f.write(t)
                done += 1

        await asyncio.gather(*(one(d, lang) for d, lang in todo))
    if done:
        logger.info("kb_i18n.warmed", translated=done, langs=langs)
    return done
