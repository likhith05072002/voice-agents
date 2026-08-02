"""Indic transcripts must never crash the logger.

Production symptom: the agent answered English fine, then went permanently
mute the moment the caller spoke Kannada/Gujarati/Hindi. Cause: the
barge-in loggers wrote the transcript RAW, and on a cp1252 console (Windows
dev) a Gujarati codepoint raises UnicodeEncodeError *from inside the log
call*. That exception escaped the engine's event loop and killed the call —
so a logging line, not the voice pipeline, was ending conversations.
"""

import io

import pytest

from src.util.logtext import safe
import src.util.logtext as logtext

INDIC = [
    "ಏನು ಸಮಾಚಾರ",                    # Kannada
    "એવો ઓછો ફવારીયો.",              # Gujarati (the one that crashed prod)
    "आप हिंदी में बात कर सकते हैं क्या?",   # Hindi
    "మీరు తెలుగు మాట్లాడతారా?",        # Telugu
]


@pytest.mark.parametrize("text", INDIC)
def test_safe_survives_cp1252_console(text, monkeypatch):
    """The real failure mode: a console that cannot encode the script."""
    monkeypatch.setattr(logtext, "_UTF8", False)
    out = safe(text, 40)
    # Must be writable to a cp1252 stream — this is the operation that threw.
    io.BytesIO(out.encode("cp1252"))          # raises if still unencodable
    # ...and the transcript must remain RECOVERABLE, unlike the old
    # encode("ascii","replace") which flattened every word to "???????".
    # (A '?' can legitimately be in the text — Hindi/Telugu questions end
    # with one — so the check is round-trip fidelity, not absence of '?'.)
    assert out.encode("ascii").decode("unicode_escape") == text[:40]


@pytest.mark.parametrize("text", INDIC)
def test_safe_keeps_real_text_on_utf8(text, monkeypatch):
    monkeypatch.setattr(logtext, "_UTF8", True)
    assert safe(text, 80) == text[:80]        # the Pi logs readable transcripts


def test_no_raw_transcript_logging_remains():
    """Guard the whole class of bug: every transcript logged must go through
    safe(). A raw text=<transcript> slice is what took production down."""
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for f in [root / "src" / "pipeline" / "turn_engine.py", root / "src" / "main.py"]:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"text=(transcript|txt|text|merged|evt\.text)\[", line) \
                    and "_safe_text" not in line:
                offenders.append(f"{f.name}:{n}: {line.strip()}")
    assert not offenders, "raw transcript logging (crashes on Indic text):\n" + "\n".join(offenders)
