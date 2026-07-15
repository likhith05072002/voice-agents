"""ElevenLabsTTSClient: raw PCM chunks flow to the audio queue with the
engine's sentinel contract; HTTP errors (e.g. 402 paid_plan_required on
library voices) end the utterance cleanly instead of hanging the turn."""

import asyncio

from src.services.tts.elevenlabs import ElevenLabsTTSClient


class _FakeResponse:
    def __init__(self, chunks: list[bytes], status: int = 200):
        self._chunks = chunks
        self.status_code = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c

    async def aread(self):
        return b'{"detail":{"code":"paid_plan_required"}}'


class _FakeClient:
    def __init__(self, chunks: list[bytes], status: int = 200):
        self._chunks, self._status = chunks, status
        self.last_url = ""

    def stream(self, method, url, json=None):
        self.last_url = url
        return _FakeResponse(self._chunks, self._status)


async def _drain(client: ElevenLabsTTSClient) -> bytes:
    out = b""
    while (chunk := await asyncio.wait_for(client.get_audio(), timeout=2)) is not None:
        out += chunk
    return out


async def test_raw_pcm_chunks_reach_queue_in_order():
    payload = [b"\x01\x02" * 100, b"", b"\x03\x04" * 50]   # empty chunk skipped
    c = ElevenLabsTTSClient("k")
    await c.connect(voice="EXAVITQu4vr4xnSDxMaL", sample_rate="16000")
    fake = _FakeClient(payload)
    c._client = fake
    await c.send_text("hello")
    assert await _drain(c) == b"\x01\x02" * 100 + b"\x03\x04" * 50
    assert "pcm_16000" in fake.last_url and "EXAVITQu4vr4xnSDxMaL" in fake.last_url


async def test_http_error_ends_utterance_cleanly():
    c = ElevenLabsTTSClient("k")
    await c.connect(voice="ljX1ZrXuDIIRVcmiVSyR")
    c._client = _FakeClient([], status=402)
    await c.send_text("hello")
    assert await _drain(c) == b""      # sentinel arrives, no chunks, no hang


async def test_phone_sample_rate_selects_pcm_8000():
    c = ElevenLabsTTSClient("k")
    await c.connect(voice="v" * 12, sample_rate="8000")
    fake = _FakeClient([b"\x00\x00"])
    c._client = fake
    await c.send_text("hi")
    await _drain(c)
    assert "pcm_8000" in fake.last_url
