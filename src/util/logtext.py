"""Log transcripts without destroying them.

Indic transcripts were being logged as ``text.encode("ascii", "replace")``,
which turns every Kannada/Hindi/Tamil word into ``???????`` — so a live
debugging session could see THAT the caller said something but never WHAT,
which is exactly the information needed when STT picks the wrong language.

The ASCII mangling existed for a real reason: a Windows console using cp1252
raises UnicodeEncodeError on Devanagari, and a crashing log line is worse
than a lossy one. So: emit the real text when the stream can carry it, and
fall back to escapes (still decodable) when it cannot — never ``?``.
"""

import sys


def _stream_is_utf8() -> bool:
    enc = getattr(sys.stdout, "encoding", "") or ""
    return "utf" in enc.lower()


_UTF8 = _stream_is_utf8()


def safe(text: str, limit: int = 200) -> str:
    """Loggable form of a transcript: readable where possible, never lossy."""
    if not text:
        return ""
    clipped = text[:limit]
    if _UTF8:
        return clipped
    # cp1252 console: \uXXXX escapes keep the script identifiable and the
    # content recoverable, unlike '?'.
    return clipped.encode("unicode_escape").decode("ascii")
