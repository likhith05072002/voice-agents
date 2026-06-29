"""Barge-in guard stack — decides whether caller speech is a real interruption.

This is the part that separates "feels human" from "stops every time I breathe".
The engine reacts to a *candidate* interruption the instant VAD fires, but the
final decision runs the transcript through this guard stack, in priority order:

  1. hard-interrupt phrase  -> always interrupt now ("stop", "wait", "ఆగు")
  2. pure backchannel       -> never interrupt ("uh-huh", "haan", "avunu", "సరే")
  3. too short / noise       -> never interrupt (click, cough, stray token)
  4. otherwise               -> real interruption

Sets are multilingual on purpose (Telugu/Hindi/Kannada/Tamil/English, both
native-script and romanized) because Sarvam STT emits native script. They are
overridable via the engine so a deployment can tune them per domain.
"""

from enum import Enum

# Sentence-final punctuation across Latin + Indic scripts, plus filler dots.
_PUNCT = ".,!?;:…।॥|\"'`()[]{}-—–।॥"


class Verdict(str, Enum):
    HARD = "hard"            # explicit stop -> interrupt immediately
    BACKCHANNEL = "backchannel"  # acknowledgement -> keep talking
    SHORT = "short"         # below min words / noise -> keep talking
    REAL = "real"           # genuine interruption -> interrupt


# Explicit "stop talking" phrases — highest priority, override everything.
HARD_INTERRUPT: frozenset[str] = frozenset({
    "stop", "stop it", "wait", "wait wait", "hold on", "hang on", "one moment",
    "no no", "shut up", "quiet", "enough",
    # Telugu
    "ఆగు", "ఆపు", "ఆగండి", "వద్దు",
    # Hindi
    "रुको", "रुकिए", "ठहरो", "रुक जाओ", "बस", "नहीं नहीं",
    # Kannada
    "ನಿಲ್ಲಿಸಿ", "ನಿಲ್ಲು", "ಬೇಡ", "ಸಾಕು",
    # Tamil
    "நிறுத்து", "நில்லு", "வேண்டாம்", "போதும்",
})

# Acknowledgement / backchannel tokens — caller is listening, not interrupting.
BACKCHANNELS: frozenset[str] = frozenset({
    # English (romanized)
    "uh-huh", "uhhuh", "uh huh", "mhm", "mm", "mmm", "mmhmm", "hmm", "hm",
    "yeah", "yep", "yup", "ok", "okay", "k", "kay", "right", "sure", "alright",
    "i see", "got it", "cool", "aha", "ah", "oh", "yes", "y²",
    # Hindi
    "हाँ", "हां", "जी", "जी हाँ", "अच्छा", "ठीक", "ठीक है", "सही", "बिल्कुल", "हम्म",
    "haan", "han", "haa", "ji", "jee", "accha", "acha", "acchaa", "theek",
    "theek hai", "sahi", "bilkul",
    # Telugu
    "అవును", "ఔను", "సరే", "సరి", "మంచిది", "అలాగే", "హా",
    "avunu", "aunu", "sare", "sari", "manchidi", "alage",
    # Kannada
    "ಹೌದು", "ಸರಿ", "ಆಯಿತು", "ಆಯ್ತು",
    "haudu", "houdu", "aytu",
    # Tamil
    "ஆமா", "ஆமாம்", "சரி", "சரிங்க", "ஓகே",
    "aama", "aamaam", "seri",
})


def normalize(text: str) -> str:
    """Lowercase + strip surrounding/embedded punctuation, collapse whitespace."""
    cleaned = "".join(" " if ch in _PUNCT else ch for ch in text.lower())
    return " ".join(cleaned.split())


def classify(
    transcript: str,
    *,
    min_words: int = 2,
    backchannels: frozenset[str] = BACKCHANNELS,
    hard_phrases: frozenset[str] = HARD_INTERRUPT,
) -> Verdict:
    """Classify a candidate interruption transcript. See module docstring."""
    norm = normalize(transcript)
    if not norm:
        return Verdict.SHORT

    # 1. Hard interrupt: any hard phrase present as a whole word/phrase.
    words = norm.split()
    word_set = set(words)
    for phrase in hard_phrases:
        if " " in phrase:
            if phrase in norm:
                return Verdict.HARD
        elif phrase in word_set:
            return Verdict.HARD

    # 2. Pure backchannel: the whole utterance is acknowledgement tokens.
    if norm in backchannels or all(w in backchannels for w in words):
        return Verdict.BACKCHANNEL

    # 3. Too short to be a real interruption (likely noise / stray STT token).
    if len(words) < min_words:
        return Verdict.SHORT

    # 4. Genuine interruption.
    return Verdict.REAL
