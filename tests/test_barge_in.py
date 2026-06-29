from src.pipeline.barge_in import classify, normalize, Verdict


def test_hard_phrases_interrupt():
    assert classify("stop") is Verdict.HARD
    assert classify("wait, hold on") is Verdict.HARD
    assert classify("ఆగు") is Verdict.HARD          # Telugu "stop"
    assert classify("रुको") is Verdict.HARD          # Hindi "wait"


def test_backchannels_do_not_interrupt():
    for token in ["uh-huh", "yeah", "okay", "hmm", "haan", "अच्छा", "avunu", "సరే", "ஆமா"]:
        assert classify(token) is Verdict.BACKCHANNEL, token


def test_multiword_backchannel_phrase():
    assert classify("yeah okay") is Verdict.BACKCHANNEL
    assert classify("haan ji") is Verdict.BACKCHANNEL


def test_short_noise_is_not_an_interruption():
    assert classify("a", min_words=2) is Verdict.SHORT
    assert classify("") is Verdict.SHORT


def test_real_interruption():
    assert classify("actually I have a different question") is Verdict.REAL
    assert classify("what about the price of silver") is Verdict.REAL


def test_hard_phrase_beats_min_words():
    # single word, but a hard phrase -> still interrupts
    assert classify("stop", min_words=5) is Verdict.HARD


def test_normalize_strips_punctuation_and_case():
    assert normalize("Yeah!!!") == "yeah"
    assert normalize("  STOP. ") == "stop"
