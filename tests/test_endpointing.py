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


def test_context_fragments_are_held_for_continuation():
    from src.pipeline.endpointing import looks_continuable
    # front-loaded context, the ask comes next breath -> hold
    assert looks_continuable("Hello. I found your website online.") is True
    assert looks_continuable("अब हम हिंदी में बात करते हैं।") is True
    # actual questions/requests -> fire immediately
    assert looks_continuable("Can you tell me what Cocolevio does?") is False
    assert looks_continuable("Tell me everything about your services in detail.") is False
    assert looks_continuable("ನಿಮ್ಮ ಕಂಪನಿ ಎಲ್ಲಿದೆ?") is False
    assert looks_continuable("ಕಂಪನಿಯ ಸೇವೆಗಳ ಬಗ್ಗೆ ಹೇಳಿ.") is False
    assert looks_continuable("रुकिए, क्या आपके पास कोई HR प्रोडक्ट है?") is False
