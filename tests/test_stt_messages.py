"""Tests for Sarvam STT message handling (incl. unhandled-type visibility)."""

import asyncio

from src.services.stt.sarvam import SarvamSTTClient, TranscriptEvent, VADEvent


def _client():
    return SarvamSTTClient("key")


async def test_data_message_becomes_transcript():
    c = _client()
    await c._handle_message({"type": "data",
                             "data": {"transcript": "నమస్తే", "language_code": "te-IN"}})
    evt = c._transcript_queue.get_nowait()
    assert isinstance(evt, TranscriptEvent)
    assert evt.text == "నమస్తే" and evt.language == "te-IN"


async def test_empty_transcript_is_dropped():
    c = _client()
    await c._handle_message({"type": "data", "data": {"transcript": "   "}})
    assert c._transcript_queue.empty()


async def test_vad_signals_become_events():
    c = _client()
    await c._handle_message({"type": "events", "data": {"signal_type": "START_SPEECH"}})
    await c._handle_message({"type": "events", "data": {"signal_type": "END_SPEECH"}})
    start = c._transcript_queue.get_nowait()
    end = c._transcript_queue.get_nowait()
    assert isinstance(start, VADEvent) and start.is_speech_start is True
    assert isinstance(end, VADEvent) and end.is_speech_start is False


async def test_unknown_type_is_logged_not_crashed_and_not_queued():
    c = _client()
    await c._handle_message({"type": "mystery", "data": {"x": 1}})
    assert c._transcript_queue.empty()
    assert "mystery" in c._seen_unhandled          # visible for the next live call


async def test_unknown_signal_type_recorded():
    c = _client()
    await c._handle_message({"type": "events", "data": {"signal_type": "SPEECH_BEGIN"}})
    assert c._transcript_queue.empty()
    assert "events/SPEECH_BEGIN" in c._seen_unhandled


async def test_unhandled_logged_once_per_kind():
    c = _client()
    await c._handle_message({"type": "mystery"})
    await c._handle_message({"type": "mystery"})
    assert len([k for k in c._seen_unhandled if k == "mystery"]) == 1
