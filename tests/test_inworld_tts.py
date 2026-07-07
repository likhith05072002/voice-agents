"""InworldTTSClient: WAV-header stripping across the stream.

Regression for the live "tap tap tap" bug: Inworld's /tts/v1/voice:stream
wraps EVERY chunk as a self-contained WAV (44B RIFF header + PCM), and the
client originally stripped only the first chunk's header — so each later
header played as ~1.4ms of garbage audio, an audible click every 0.5s.
"""

import base64
import json
import struct

from src.services.tts.inworld import InworldTTSClient, _strip_wav_header


def _wav_chunk(pcm: bytes) -> bytes:
    """A minimal standard 44-byte-header WAV wrapping `pcm`, mirroring the
    self-contained chunks Inworld streams."""
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)


def test_strip_standard_header():
    pcm = bytes(range(64))
    assert _strip_wav_header(_wav_chunk(pcm)) == pcm


def test_strip_header_with_extra_subchunk():
    # LIST metadata between fmt and data must not leak into the audio.
    pcm = b"\x01\x02" * 20
    wav = (b"RIFF" + struct.pack("<I", 0) + b"WAVE"
           + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
           + b"LIST" + struct.pack("<I", 4) + b"INFO"
           + b"data" + struct.pack("<I", len(pcm)) + pcm)
    assert _strip_wav_header(wav) == pcm


def test_non_riff_chunk_passes_through():
    pcm = b"\x10\x20" * 100
    assert _strip_wav_header(pcm) == pcm


class _FakeResponse:
    """Stands in for httpx's streaming response: NDJSON lines of base64 WAVs."""

    status_code = 200

    def __init__(self, chunks: list[bytes]):
        self._lines = [
            json.dumps({"result": {"audioContent": base64.b64encode(c).decode()}})
            for c in chunks
        ]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeClient:
    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    def stream(self, method, url, json=None):
        return _FakeResponse(self._chunks)


async def test_every_chunk_header_stripped():
    """The regression: with 3 WAV-wrapped chunks, the audio queue must yield
    pure PCM — no RIFF bytes from ANY chunk, first or later."""
    payloads = [b"\x11\x11" * 800, b"\x22\x22" * 800, b"\x33\x33" * 700]
    client = InworldTTSClient("test-key")
    await client.connect(voice="test-voice", sample_rate="16000")
    client._client = _FakeClient([_wav_chunk(p) for p in payloads])

    await client.send_text("hello")
    out = b""
    while (chunk := await client.get_audio()) is not None:
        assert b"RIFF" not in chunk, "WAV header leaked into the audio stream"
        out += chunk
    assert out == b"".join(payloads)
