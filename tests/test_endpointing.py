"""Tests for the semantic endpointing classifier."""

from src.pipeline.endpointing import looks_incomplete


def test_sentence_final_punctuation_is_complete():
    assert looks_incomplete("What is the gold rate today?") is False
    assert looks_incomplete("Tell me the price.") is False
    assert looks_incomplete("नमस्ते।") is False


def test_trailing_conjunction_is_incomplete():
    assert looks_incomplete("I want to know the price and") is True
    assert looks_incomplete("gold rate today but") is True


def test_trailing_preposition_is_incomplete():
    assert looks_incomplete("I want to know about") is True
    assert looks_incomplete("can you tell me about the") is True


def test_dangling_comma_or_ellipsis_is_incomplete():
    assert looks_incomplete("so the thing is,") is True
    assert looks_incomplete("let me think…") is True
    assert looks_incomplete("hmm...") is True


def test_plain_complete_clause_is_complete():
    # No final punctuation but a normal content word -> don't add latency.
    assert looks_incomplete("twenty two carat gold price") is False
    assert looks_incomplete("show me silver") is False


def test_empty_is_complete():
    assert looks_incomplete("") is False
    assert looks_incomplete("   ") is False


def test_multilingual_continuation_words():
    assert looks_incomplete("मुझे जानना है की") is True       # Hindi "ki"
    assert looks_incomplete("నాకు కావాలి మరియు") is True       # Telugu "mariyu" (and)
    assert looks_incomplete("enakku theriyanum aanal") is True  # Tamil romanized "but"
