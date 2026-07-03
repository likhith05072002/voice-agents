"""Tests for call records, recorder, and stores."""

from src.persistence.records import CallRecord, CallRecorder, Turn, CallStats
from src.persistence.store import InMemoryCallStore, SqliteCallStore


# ─── CallRecord ───

def test_record_computes_duration_and_avg_latency():
    r = CallRecord(call_id="c1", agent_id="a", started_at=100.0, ended_at=130.0)
    r.metrics = [{"perceived_ms": 800}, {"perceived_ms": 600}]
    r.turns = [Turn("user", "hi", 0.0), Turn("assistant", "hello", 0.5)]
    assert r.duration_s == 30.0
    assert r.avg_perceived_ms() == 700.0
    assert r.turn_count == 2


def test_record_row_roundtrip_preserves_everything():
    r = CallRecord(call_id="c2", agent_id="jeweller", from_number="+91999",
                   to_number="+91400", started_at=1.0, ended_at=2.0,
                   turns=[Turn("user", "నమస్తే", 0.1)],
                   metrics=[{"perceived_ms": 500}], outcome="completed",
                   metadata={"campaign": "diwali"})
    back = CallRecord.from_row(r.to_row())
    assert back.turns[0].text == "నమస్తే"            # unicode survives
    assert back.metrics == [{"perceived_ms": 500}]
    assert back.metadata["campaign"] == "diwali"
    assert back.agent_id == "jeweller"


# ─── CallRecorder ───

def test_recorder_collects_and_finalizes():
    ticks = iter([0.0, 1.0, 2.5])   # t0, then two transcript times
    rec = CallRecorder("c", "a", started_at=100.0, clock=lambda: next(ticks))
    rec.transcript("user", "hi")
    rec.transcript("assistant", "hello")
    rec.metric({"perceived_ms": 700})
    rec.metric({})                  # empty dropped
    record = rec.finalize(ended_at=130.0, outcome="completed")
    assert [t.t for t in record.turns] == [1.0, 2.5]
    assert record.metrics == [{"perceived_ms": 700}]
    assert record.outcome == "completed" and record.ended_at == 130.0


# ─── stores ───

async def test_inmemory_store_save_and_filter():
    s = InMemoryCallStore()
    await s.save(CallRecord(call_id="1", agent_id="a"))
    await s.save(CallRecord(call_id="2", agent_id="b"))
    await s.save(CallRecord(call_id="3", agent_id="a"))
    assert len(await s.recent(agent_id="a")) == 2
    assert (await s.recent(limit=1))[0].call_id == "3"      # most recent first


async def test_sqlite_store_persists_and_queries(tmp_path):
    db = str(tmp_path / "calls.db")
    s = SqliteCallStore(db)
    r = CallRecord(call_id="c1", agent_id="jeweller", started_at=10.0, ended_at=40.0,
                   turns=[Turn("user", "hi", 0.0)], metrics=[{"perceived_ms": 650}],
                   outcome="completed")
    await s.save(r)

    # New store instance on the same file -> data is durable.
    s2 = SqliteCallStore(db)
    got = await s2.recent(agent_id="jeweller")
    assert len(got) == 1
    assert got[0].call_id == "c1"
    assert got[0].avg_perceived_ms() == 650.0
    assert got[0].turns[0].text == "hi"


async def test_sqlite_upsert_dedupes_by_call_id(tmp_path):
    s = SqliteCallStore(str(tmp_path / "c.db"))
    await s.save(CallRecord(call_id="dup", agent_id="a", outcome="partial"))
    await s.save(CallRecord(call_id="dup", agent_id="a", outcome="completed"))
    rows = await s.recent()
    assert len(rows) == 1 and rows[0].outcome == "completed"


# ─── analytics aggregation ───

async def test_stats_aggregate_inmemory():
    s = InMemoryCallStore()
    await s.save(CallRecord(call_id="1", agent_id="a", started_at=0, ended_at=10,
                            metrics=[{"perceived_ms": 800}], outcome="completed"))
    await s.save(CallRecord(call_id="2", agent_id="a", started_at=0, ended_at=20,
                            metrics=[{"perceived_ms": 600}], outcome="completed"))
    await s.save(CallRecord(call_id="3", agent_id="a", started_at=0, ended_at=30,
                            outcome="abandoned"))
    st = await s.stats(agent_id="a")
    assert st.total_calls == 3
    assert st.avg_duration_s == 20.0
    assert st.avg_perceived_ms == 700.0                  # only the 2 with metrics
    assert st.by_outcome == {"completed": 2, "abandoned": 1}


async def test_stats_time_window():
    s = InMemoryCallStore()
    await s.save(CallRecord(call_id="old", agent_id="a", started_at=100))
    await s.save(CallRecord(call_id="new", agent_id="a", started_at=200))
    assert (await s.stats(agent_id="a", since=150)).total_calls == 1


async def test_sqlite_stats(tmp_path):
    s = SqliteCallStore(str(tmp_path / "c.db"))
    await s.save(CallRecord(call_id="1", agent_id="a", started_at=0, ended_at=10,
                            metrics=[{"perceived_ms": 500}], outcome="completed"))
    await s.save(CallRecord(call_id="2", agent_id="b", started_at=0, ended_at=20,
                            outcome="completed"))
    a = await s.stats(agent_id="a")
    assert a.total_calls == 1 and a.avg_duration_s == 10.0 and a.avg_perceived_ms == 500.0
    allst = await s.stats()
    assert allst.total_calls == 2
    assert allst.by_outcome.get("completed") == 2


# ─── transcript search ───

async def test_search_finds_matching_transcript():
    s = InMemoryCallStore()
    await s.save(CallRecord(call_id="1", agent_id="a",
                            turns=[Turn("user", "I want a refund please", 0.0)]))
    await s.save(CallRecord(call_id="2", agent_id="a",
                            turns=[Turn("user", "gold price today", 0.0)]))
    hits = await s.search("refund")
    assert [r.call_id for r in hits] == ["1"]
    assert await s.search("") == []


async def test_sqlite_search_matches_and_filters(tmp_path):
    s = SqliteCallStore(str(tmp_path / "c.db"))
    await s.save(CallRecord(call_id="1", agent_id="jeweller",
                            turns=[Turn("user", "పాత బంగారం exchange అవుతుందా", 0.0)]))
    await s.save(CallRecord(call_id="2", agent_id="dental",
                            turns=[Turn("user", "book a cleaning appointment", 0.0)]))
    # unicode search works
    assert [r.call_id for r in await s.search("బంగారం")] == ["1"]
    # agent filter works
    assert await s.search("appointment", agent_id="jeweller") == []
    assert [r.call_id for r in await s.search("appointment", agent_id="dental")] == ["2"]


async def test_sqlite_search_escapes_like_wildcards(tmp_path):
    s = SqliteCallStore(str(tmp_path / "c.db"))
    await s.save(CallRecord(call_id="1", agent_id="a",
                            turns=[Turn("user", "100% genuine gold", 0.0)]))
    await s.save(CallRecord(call_id="2", agent_id="a",
                            turns=[Turn("user", "handmade bangles", 0.0)]))
    # '%' must be treated literally, not as a match-everything wildcard
    hits = await s.search("100%")
    assert [r.call_id for r in hits] == ["1"]


# ─── engine integration: a real turn becomes a CallRecord ───

import asyncio  # noqa: E402

from src.pipeline.turn_engine import TurnEngine  # noqa: E402
from src.services.stt.sarvam import TranscriptEvent  # noqa: E402
from src.services.llm.sarvam import SentenceEvent  # noqa: E402


class _STT:
    def __init__(self): self.q = asyncio.Queue()
    async def get_event(self): return await self.q.get()


class _LLM:
    async def generate_sentences(self, messages, queue):
        await queue.put(SentenceEvent(text="Hello there. ", is_first=True, timestamp=0.0))
        await queue.put(None)
        return "Hello there. "
    def cancel(self): ...


class _TTS:
    def __init__(self): self._p = []
    async def reset(self): self._p = []
    async def send_text(self, t): self._p = [b"\x01\x00" * 160, None]
    async def flush(self): ...
    async def get_audio(self): return self._p.pop(0) if self._p else None


async def test_engine_turn_populates_call_record(tmp_path):
    rec = CallRecorder("call-1", "jeweller", started_at=0.0, clock=lambda: 0.0)
    engine = TurnEngine(stt=_STT(), llm=_LLM(), tts=_TTS(),
                        send_media=lambda f: _noop(),
                        system_prompt="s", greeting_text="", frame_pace_s=0,
                        on_transcript=rec.transcript, on_metrics=rec.metric)
    run = asyncio.create_task(engine.run())
    await engine.stt.q.put(TranscriptEvent(text="hi", is_final=True, language="en", timestamp=0.0))
    await _wait(lambda: any(t.role == "assistant" for t in rec.record.turns))
    await engine.stt.q.put(None)
    await asyncio.wait_for(run, timeout=4.0)

    record = rec.finalize(ended_at=12.0)
    assert [t.role for t in record.turns] == ["user", "assistant"]
    assert record.turns[1].text == "Hello there. "
    assert record.metrics and "perceived_ms" in record.metrics[0]

    # and it persists
    store = SqliteCallStore(str(tmp_path / "calls.db"))
    await store.save(record)
    assert (await store.recent(agent_id="jeweller"))[0].call_id == "call-1"


async def _noop():
    return None


async def _wait(pred, timeout=2.0):
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if pred():
            return True
        await asyncio.sleep(0.005)
    raise AssertionError("condition not met")
