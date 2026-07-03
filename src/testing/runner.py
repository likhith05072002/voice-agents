"""Test-run orchestration: drive a scenario over loopback OR a real phone call.

Loopback: the tester connects to our own /media-stream as a fake Telnyx.
PSTN:     the tester places a REAL call from its own number to the main agent's
          number; when Telnyx opens the tester leg's media stream, main.py
          attaches it here and the same scenario logic drives a real phone call.

Live: the dashboard polls ``status()`` and can subscribe to the mixed
conversation audio through ``core.subscribe()`` (see /test/listen).
"""

from __future__ import annotations

import asyncio
import time
import uuid

import structlog

from src.testing.scenario import Scenario
from src.testing.core import TesterCore
from src.testing.transports import LoopbackTransport, PstnBridge
from src.testing.caller import render_utterances

logger = structlog.get_logger()


class TestRun:
    def __init__(self, scenario: Scenario, *, api_key: str, get_record,
                 ws_url: str = "", transport: str = "loopback",
                 place_call=None, hangup_call=None,
                 wav_dir: str = "data/test_runs"):
        self.test_id = uuid.uuid4().hex[:10]
        self.scenario = scenario
        self.api_key = api_key
        self.get_record = get_record          # async (call_id, after_ts) -> CallRecord | None
        self.ws_url = ws_url
        self.transport = transport            # "loopback" | "pstn"
        self.place_call = place_call          # async () -> ccid (tester leg)
        self.hangup_call = hangup_call        # async (ccid) -> None
        self.wav_path = f"{wav_dir}/{self.test_id}.wav"
        self.core = TesterCore()              # exists up-front so /listen can attach
        self.state = "pending"
        self.error = ""
        self.events: list[dict] = []
        self.started_ts = 0.0
        self.tester_ccid = ""
        self._bridge: PstnBridge | None = None
        self._bridge_ready = asyncio.Event()
        self._finished = asyncio.Event()
        self.steps: list[dict] = [
            {"question": s.say, "type": "barge_in" if s.barge_in else "say",
             "status": "pending", "latency_ms": None, "time_to_silence_ms": None,
             "answer": "", "check": "none"}
            for s in scenario.steps
        ]
        self.greeting_ms: float | None = None

    # ─── observability ───

    def _event(self, msg: str) -> None:
        self.events.append({"t": round(time.time(), 2), "msg": msg})
        logger.info("test.event", test_id=self.test_id,
                    msg=msg.encode("ascii", "replace").decode())

    def status(self) -> dict:
        return {
            "test_id": self.test_id, "scenario": self.scenario.name,
            "transport": self.transport, "state": self.state, "error": self.error,
            "greeting_ms": self.greeting_ms, "steps": self.steps,
            "events": self.events[-30:],
        }

    # ─── PSTN attachment (called by main.py when the tester leg's media opens) ───

    async def attach_pstn(self, websocket, encoding: str) -> None:
        """Run the tester's side of the real call on this media stream. Returns
        when the scenario has finished (main.py then cleans up the leg)."""
        self._event(f"tester leg media stream attached (codec {encoding})")
        self._bridge = PstnBridge(self.core, websocket, encoding)
        self._bridge.start()
        self._bridge_ready.set()
        # Hold the socket open until the scenario is done or the call drops.
        done = asyncio.create_task(self._finished.wait())
        drop = asyncio.create_task(self._bridge.closed.wait())
        await asyncio.wait({done, drop}, return_when=asyncio.FIRST_COMPLETED)
        done.cancel()
        drop.cancel()
        await self._bridge.stop()

    # ─── the run ───

    async def run(self) -> None:
        self.started_ts = time.time()
        try:
            await self._run()
        except Exception as e:  # noqa: BLE001 — a failed run must report, not vanish
            self.state = "failed"
            self.error = f"{type(e).__name__}: {e}"
            self._event(f"FAILED: {self.error}")
        finally:
            self._finished.set()
            if self.tester_ccid and self.hangup_call:
                try:
                    await self.hangup_call(self.tester_ccid)
                    self._event("hung up the tester leg")
                except Exception:  # noqa: BLE001
                    pass
            try:
                self.core.save_wav(self.wav_path)
            except Exception:  # noqa: BLE001
                pass

    async def _run(self) -> None:
        self.state = "rendering"
        self._event("rendering caller voice (cached after first run)")
        texts = [s.say for s in self.scenario.steps if s.say]
        audio = await render_utterances(texts, self.scenario.language,
                                        self.scenario.caller_voice, self.api_key)

        loopback = None
        if self.transport == "pstn":
            self.state = "dialing"
            self._event("placing REAL call from tester number to main agent")
            self.tester_ccid = await self.place_call(self.test_id)
            self._event(f"dialing (leg {self.tester_ccid[:16]}…), waiting for answer")
            try:
                await asyncio.wait_for(self._bridge_ready.wait(), timeout=40.0)
            except asyncio.TimeoutError:
                raise RuntimeError("main agent never answered / media never attached")
        else:
            self.state = "calling"
            loopback = LoopbackTransport(self.core, self.ws_url, f"test-{self.test_id}")
            await loopback.connect()
        t_connect = time.perf_counter()
        self._event("connected, waiting for the main agent's greeting")

        t_greet = await self.core.wait_new_audio(0, timeout=25.0)
        if t_greet is None:
            raise RuntimeError("agent never played the greeting")
        self.greeting_ms = round((t_greet - t_connect) * 1000)
        self._event(f"greeting first audio after {self.greeting_ms} ms")
        await self.core.wait_quiet(quiet_s=1.0)

        self.state = "running"
        for i, step in enumerate(self.scenario.steps):
            row = self.steps[i]
            row["status"] = "asking"
            if step.barge_in:
                await self._barge_step(step, row, audio)
            else:
                await self._say_step(step, row, audio)
            row["status"] = "answered" if row["latency_ms"] is not None else "no_reply"

        await self.core.wait_quiet(quiet_s=1.2, timeout=20.0)
        if loopback is not None:
            await loopback.close()
        self._event("call ended, verifying answers against the call record")

        self.state = "verifying"
        await self._verify(f"test-{self.test_id}")
        self.state = "done"
        self._event("done")

    async def _say_step(self, step, row, audio) -> None:
        # A polite caller ALWAYS waits for the line to settle before asking —
        # answer audio can still be in transit-flight even when the agent
        # sounds momentarily quiet (caused answer/question desync on live runs).
        await self.core.wait_quiet(quiet_s=1.6, timeout=25.0)
        frames_before = self.core.agent_frames
        self.core.speak(audio[step.say])
        t_stop = await self.core.wait_done_speaking()
        self._event(f"asked: {step.say[:48]}")
        row["status"] = "waiting"
        t_first = await self.core.wait_new_audio(frames_before, timeout=step.max_wait_s)
        if t_first is None or t_stop is None:
            self._event("no reply within budget")
            # Re-sync: if the answer arrives late, let it finish so it can't
            # bleed into the NEXT step's measurement (off-by-one on live runs).
            await self.core.wait_quiet(quiet_s=1.2, timeout=20.0)
            return
        if t_first > t_stop:
            row["latency_ms"] = round((t_first - t_stop) * 1000)
            self._event(f"reply started after {row['latency_ms']} ms")
        else:
            self._event("reply overlapped the question — latency not measured")
        if step.await_full_answer:
            await self.core.wait_quiet(quiet_s=1.6, timeout=25.0)

    async def _barge_step(self, step, row, audio) -> None:
        t0 = time.perf_counter()
        while not self.core.agent_speaking() and time.perf_counter() - t0 < 10:
            await asyncio.sleep(0.02)
        await asyncio.sleep(step.trigger_after_s)
        if not self.core.agent_speaking():
            # The answer ended before we could interrupt — a barge-in premise
            # failure, not an agent fault. Fall back to a normal question so
            # the step still measures something meaningful.
            self._event("barge-in skipped (agent already finished) — asking normally")
            row["type"] = "say"
            await self._say_step(step, row, audio)
            return
        t_barge = time.perf_counter()
        self.core.speak(audio[step.say])
        self._event(f"BARGE-IN: talking over the agent: {step.say[:40]}")
        silent_at = None
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 12:
            if self.core.agent_quiet_for(0.35):
                silent_at = self.core.last_frame_t
                break
            await asyncio.sleep(0.02)
        if silent_at is not None:
            row["time_to_silence_ms"] = max(0, round((silent_at - t_barge) * 1000))
            self._event(f"agent went silent {row['time_to_silence_ms']} ms after barge-in")
        t_stop = await self.core.wait_done_speaking()
        t_first = await self.core.wait_new_audio(self.core.agent_frames,
                                                 timeout=step.max_wait_s)
        if t_first is not None and t_stop is not None and t_first > t_stop:
            row["latency_ms"] = round((t_first - t_stop) * 1000)
        await self.core.wait_quiet(quiet_s=1.0, timeout=25.0)

    async def _verify(self, call_id: str) -> None:
        # The record is saved during the media stream's teardown, which races
        # our close — poll briefly. For PSTN the main leg has its own ccid, so
        # the lookup falls back to "newest record since the test started".
        record = None
        for _ in range(10):
            record = await self.get_record(call_id, self.started_ts)
            if record is not None:
                break
            await asyncio.sleep(0.5)
        if record is None:
            self._event("no call record found (persistence off?) — skipping checks")
            return
        # Build (user line -> concatenated assistant reply) groups, then pair
        # each step to its answer by MATCHING the question text to the user
        # line (token overlap). Order-based pairing shifted rows whenever barge
        # timing interleaved the feed — content matching is timing-proof.
        groups: list[tuple[str, str]] = []      # (user_text, answer_text)
        for turn in record.turns:
            if turn.role == "user":
                groups.append((turn.text, ""))
            elif turn.role == "assistant" and groups:
                q, a = groups[-1]
                groups[-1] = (q, f"{a} {turn.text}".strip())

        def toks(s: str) -> set:
            return {w.strip(".,?!").lower() for w in s.split() if len(w) > 2}

        used: set[int] = set()
        for row in self.steps:
            want = toks(row["question"])
            best_i, best_score = -1, 0.0
            for i, (q, a) in enumerate(groups):
                if i in used or not a:
                    continue
                got = toks(q)
                score = len(want & got) / max(1, len(want))
                if score > best_score:
                    best_i, best_score = i, score
            if best_i >= 0 and best_score >= 0.3:   # STT-garble tolerant
                used.add(best_i)
                answer = groups[best_i][1]
                row["answer"] = answer[:300]
                step_keywords = next(
                    (s.expect_keywords for s in self.scenario.steps
                     if s.say == row["question"]), [])
                if step_keywords:
                    found = all(k.lower() in answer.lower() for k in step_keywords)
                    row["check"] = "pass" if found else "fail"
