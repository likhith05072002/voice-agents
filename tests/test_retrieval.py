"""Tests for the RAG knowledge retriever."""

from src.agent.retrieval import KnowledgeBase, build_demo_kb, _tokens


# ─── iteration 1: basic relevance ───

def test_retrieves_relevant_doc():
    kb = build_demo_kb()
    out = kb.retrieve("do you do old gold exchange?")
    assert any("old gold exchange" in d.lower() for d in out)


def test_retrieves_hallmark_doc():
    kb = build_demo_kb()
    out = kb.retrieve("is the gold hallmarked and certified")
    assert any("hallmark" in d.lower() for d in out)


# ─── iteration 2: edge cases ───

def test_no_match_returns_empty():
    kb = build_demo_kb()
    assert kb.retrieve("what is the weather on mars") == []


def test_empty_query_returns_empty():
    assert build_demo_kb().retrieve("") == []


def test_respects_k():
    kb = build_demo_kb()
    assert len(kb.retrieve("gold rate exchange certificate scheme", k=1)) <= 1
    assert len(kb.retrieve("gold rate exchange certificate scheme", k=3)) <= 3


# ─── iteration 3: scoring quality ───

def test_ranks_more_relevant_first():
    kb = KnowledgeBase([
        "We sell silver anklets and toe rings.",
        "Gold savings scheme: pay 11 months get the 12th free.",
    ])
    out = kb.retrieve("tell me about the gold savings scheme", k=2)
    assert "savings scheme" in out[0].lower()


def test_rare_word_outweighs_common_word():
    kb = KnowledgeBase([
        "the the the the the gold",          # common words, one 'gold'
        "hallmark certification details here",
    ])
    # 'hallmark' is rarer/more specific than 'gold' here
    out = kb.retrieve("hallmark", k=1)
    assert "hallmark" in out[0].lower()


def test_tokenizer_handles_native_script():
    # Indic tokens also emit a ~prefix pseudo-token (light stemming for
    # case endings); short tokens don't.
    assert _tokens("బంగారం ధర") == ["బంగారం", "~బంగా", "ధర"]


def test_indic_normalization_unifies_spelling_variants():
    # abbreviation dots and anusvara/chandrabindu variants must match
    assert set(_tokens("एच.आर.")) & set(_tokens("एचआर"))
    assert set(_tokens("सेवाएँ")) & set(_tokens("सेवाएं"))


def test_multilingual_variants_retrieved_in_caller_language():
    docs = ["Products: AcmeHR, a recruitment automation product.",
            "Acme is based in Austin, Texas."]
    translations = {"hi-IN": [
        "उत्पाद: कोकोलिवियोएचआर, एक भर्ती स्वचालन उत्पाद।",
        "कोकोलिवियो ऑस्टिन, टेक्सास में स्थित है।"]}
    kb = KnowledgeBase(docs, translations=translations)
    # Hindi query matches the Hindi variant and returns IT (caller language)
    out = kb.retrieve("क्या आपके पास भर्ती स्वचालन उत्पाद है?")
    assert out and "भर्ती" in out[0]
    # English still returns the original
    out = kb.retrieve("do you have a recruitment product?")
    assert out and out[0].startswith("Products:")


def test_cross_script_query_falls_back_to_whole_kb():
    # A Devanagari query can never token-match English docs; an empty FACTS
    # section made the live agent deny AcmeHR existed. Whole-KB fallback.
    kb = KnowledgeBase([
        "Acme is a technology consulting company in Austin.",
        "Products: AcmeHR, a recruitment automation product.",
    ])
    out = kb.retrieve("क्या आपके पास कोई एचआर प्रोडक्ट है?")
    assert any("AcmeHR" in d for d in out)
    # English queries keep precise retrieval (no fallback flood)...
    assert len(kb.retrieve("do you have an HR product?")) == 1
    # ...and irrelevant English queries still inject nothing.
    assert kb.retrieve("what is the weather like") == []


# ─── iteration 5: engine integration ───

import asyncio  # noqa: E402

from src.pipeline.turn_engine import TurnEngine  # noqa: E402
from src.services.stt.sarvam import TranscriptEvent  # noqa: E402
from src.services.llm.sarvam import SentenceEvent  # noqa: E402


class _STT:
    def __init__(self): self.q = asyncio.Queue()
    async def get_event(self): return await self.q.get()


class _CaptureLLM:
    def __init__(self): self.system = ""
    async def generate_sentences(self, messages, queue):
        self.system = messages[0]["content"]
        await queue.put(SentenceEvent(text="ok. ", is_first=True, timestamp=0.0))
        await queue.put(None)
        return "ok. "
    def cancel(self): ...


class _TTS:
    def __init__(self): self._p = []
    async def reset(self): self._p = []
    async def send_text(self, t): self._p = [b"\x01\x00" * 160, None]
    async def flush(self): ...
    async def get_audio(self): return self._p.pop(0) if self._p else None


async def test_engine_injects_retrieved_knowledge():
    llm = _CaptureLLM()
    engine = TurnEngine(stt=_STT(), llm=llm, tts=_TTS(),
                        send_media=lambda f: _noop(),
                        system_prompt="base", greeting_text="", frame_pace_s=0,
                        knowledge=build_demo_kb())
    run = asyncio.create_task(engine.run())
    await engine.stt.q.put(TranscriptEvent(text="do you do old gold exchange",
                                           is_final=True, language="en", timestamp=0.0))
    await _wait(lambda: any(h["role"] == "assistant" for h in engine.history))
    await engine.stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)

    assert "FACTS" in llm.system
    assert "old gold exchange" in llm.system.lower()


async def _noop():
    return None


async def _wait(pred, timeout=2.0):
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if pred():
            return True
        await asyncio.sleep(0.005)
    raise AssertionError("condition not met")
