"""Tests for first-chunk-early sentence flushing (time-to-first-audio)."""

from src.services.llm.sarvam import _flush_boundary


def test_first_chunk_flushes_on_clause_boundary():
    # First chunk: a comma past the short minimum is enough to start TTS.
    assert _flush_boundary("Namaskaram,", is_first=True) is True


def test_first_chunk_not_flushed_when_too_short():
    assert _flush_boundary("Hi,", is_first=True) is False


def test_later_chunk_ignores_clause_boundary():
    # After the first chunk, a comma must NOT flush — wait for a real sentence.
    assert _flush_boundary("a fairly long clause, that continues", is_first=False) is False


def test_later_chunk_flushes_on_sentence_end():
    assert _flush_boundary("This is a complete sentence.", is_first=False) is True


def test_trailing_whitespace_is_tolerated():
    assert _flush_boundary("Welcome to the shop.  \n", is_first=False) is True


def test_no_boundary_no_flush():
    assert _flush_boundary("still going on and on", is_first=True) is False


def test_digit_boundaries_do_not_split():
    # "at 7:00 PM" was split into "at 7:" + "00 PM." on a real call.
    assert _flush_boundary("a table for four tomorrow at 7:", is_first=True) is False
    assert _flush_boundary("the price is 7,", is_first=True) is False
    # a normal clause comma still flushes the first chunk
    assert _flush_boundary("Namaskaram,", is_first=True) is True


def test_abbreviations_do_not_split_sentences():
    # "Rs." split made TTS pause mid-price on a real call — must not flush.
    assert _flush_boundary("24 carat gold is Rs.", is_first=False) is False
    assert _flush_boundary("please ask for Dr.", is_first=False) is False
    # but a genuine sentence ending in a normal word still flushes
    assert _flush_boundary("24 carat gold is Rs. 7800 per gram.", is_first=False) is True


def test_first_chunk_emits_earlier_than_old_behaviour():
    """Simulate a streamed first sentence token-by-token and compare the char
    index at which the first chunk would flush: clause-early vs sentence-only."""
    text = "Namaskaram, Nama Srinivasa Jewellery ki swagatam. Meeku emi sahayam?"

    def first_flush_index(is_first_rules: bool) -> int:
        buf = ""
        for i, ch in enumerate(text, start=1):
            buf += ch
            # old behaviour = always sentence rules; new = clause rules for first
            if _flush_boundary(buf, is_first=is_first_rules):
                return i
        return len(text)

    new_idx = first_flush_index(is_first_rules=True)    # clause-early
    old_idx = first_flush_index(is_first_rules=False)   # sentence-only
    assert new_idx < old_idx                            # first audio starts sooner
    # comma is at char 11; first full stop is at ~49
    assert new_idx == 11
