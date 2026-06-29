"""Non-blocking turn-taking & barge-in engine.

Replaces the old half-duplex orchestrator whose turn loop blocked inside a single
coroutine for the entire turn, making the agent deaf while it spoke and rendering
barge-in unreachable.

Design — centralised-interrupt FSM with a guard stack:

  - The event loop ONLY reads STT events; it never blocks on a turn.
  - Each user turn runs as its own cancellable ``asyncio.Task``.
  - Playback is PACED at real time and the pump can be PAUSED. The moment VAD
    detects caller speech while we hold the floor, we pause the pump — the caller
    hears near-instant silence (time-to-stop ~= one frame). That is a *candidate*
    interruption, not yet a commitment.
  - The candidate is then judged by the guard stack (``barge_in.classify``):
        * hard phrase ("stop"/"ఆగు")  -> confirm interrupt
        * backchannel ("uh-huh"/"haan") -> FALSE alarm, resume from where we paused
        * too short / noise            -> FALSE alarm, resume
        * real speech                  -> confirm interrupt
    If no transcript arrives within ``false_timeout_s`` we resume (recovery).
  - Confirming an interrupt runs the same four steps every time, in order:
        1. cancel the LLM stream
        2. unwind the TTS producer (task cancellation)
        3. flush the playback queue      <- the step whose absence = "won't stop"
        4. truncate history to audio actually PLAYED (tracked by the pump)

Dependencies (STT/LLM/TTS) are injected and audio leaves via a ``send_media``
callback, so the whole engine is driven by fakes in tests without any network.
"""

import asyncio
import audioop
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum

import structlog

from src.services.stt.sarvam import TranscriptEvent, VADEvent
from src.services.llm.sarvam import SentenceEvent
from src.pipeline.barge_in import classify, Verdict, BACKCHANNELS, HARD_INTERRUPT

logger = structlog.get_logger()

# 20ms of mu-law @ 8kHz = 160 bytes.
FRAME_BYTES = 160
FRAME_PACE_S = 0.02

_END = object()  # end-of-utterance marker on the playback queue


@dataclass
class _SpokenMark:
    """Queued after a sentence's frames; the pump appends it to the played text
    only once those frames have actually been sent to the carrier."""
    text: str


class State(Enum):
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


class TurnEngine:
    """Drives one phone call: STT events -> LLM -> TTS -> paced playback."""

    def __init__(
        self,
        *,
        stt,
        llm,
        tts,
        send_media: Callable[[bytes], Awaitable[None]],
        system_prompt: str,
        greeting_text: str = "",
        filler=None,
        enable_fillers: bool = False,
        frame_pace_s: float = FRAME_PACE_S,
        min_words: int = 2,
        false_timeout_s: float = 1.2,
        enable_recovery: bool = True,
        backchannels=BACKCHANNELS,
        hard_phrases=HARD_INTERRUPT,
    ) -> None:
        self.stt = stt
        self.llm = llm
        self.tts = tts
        self.send_media = send_media
        self.system_prompt = system_prompt
        self.greeting_text = greeting_text
        self.filler = filler
        self.enable_fillers = enable_fillers
        self.frame_pace_s = frame_pace_s

        # guard-stack tunables
        self.min_words = min_words
        self.false_timeout_s = false_timeout_s
        self.enable_recovery = enable_recovery
        self.backchannels = backchannels
        self.hard_phrases = hard_phrases

        self.state = State.LISTENING
        self.history: list[dict] = []

        self._playback_queue: asyncio.Queue = asyncio.Queue()
        self._pump_task: asyncio.Task | None = None
        self._current_turn: asyncio.Task | None = None
        self._turn_done = asyncio.Event()

        # Pump gate: set = playing, clear = paused (candidate interruption).
        self._pump_gate = asyncio.Event()
        self._pump_gate.set()

        # Candidate-interruption state.
        self._candidate = False
        self._candidate_timer: asyncio.Task | None = None
        self._candidate_t0 = 0.0

        # Text actually PLAYED (pump-maintained) for the in-flight turn.
        self._spoken = ""

    # ─── lifecycle ───

    async def run(self) -> None:
        self._pump_task = asyncio.create_task(self._playback_pump())
        if self.greeting_text:
            self._current_turn = asyncio.create_task(self._do_greeting())
        try:
            await self._event_loop()
        finally:
            if self._current_turn and not self._current_turn.done():
                try:
                    await asyncio.wait_for(self._current_turn, timeout=5.0)
                except Exception:
                    pass
            if self._candidate_timer:
                self._candidate_timer.cancel()
            if self._pump_task:
                self._pump_task.cancel()

    async def _event_loop(self) -> None:
        while True:
            evt = await self.stt.get_event()
            if evt is None:
                break

            if isinstance(evt, VADEvent):
                # Caller speech onset while we hold the floor -> pause and judge.
                if evt.is_speech_start and self.state == State.SPEAKING and not self._candidate:
                    self._begin_candidate()

            elif isinstance(evt, TranscriptEvent):
                txt = evt.text.strip()
                if not txt:
                    continue
                if self._candidate:
                    await self._resolve_candidate(txt)
                elif self.state in (State.SPEAKING, State.THINKING):
                    # Transcript without a pending candidate (race, or THINKING):
                    # judge directly; only act if it's a genuine interruption.
                    verdict = self._classify(txt)
                    if verdict in (Verdict.HARD, Verdict.REAL):
                        await self._confirm_interrupt(txt)
                    # backchannel/short while speaking -> ignore, keep talking
                else:
                    self._start_turn(txt)

    # ─── candidate interruption (pause -> judge -> resume or confirm) ───

    def _begin_candidate(self) -> None:
        """Pause playback the instant VAD fires. Caller hears silence now; we
        decide whether it sticks once the transcript lands."""
        self._candidate = True
        self._candidate_t0 = time.perf_counter()
        self._pump_gate.clear()  # pump stops before the next frame
        logger.info("barge_in.candidate", note="paused, awaiting transcript")
        if self.enable_recovery:
            self._candidate_timer = asyncio.create_task(self._candidate_timeout())

    async def _candidate_timeout(self) -> None:
        """False-interruption recovery: VAD fired but no real words arrived."""
        try:
            await asyncio.sleep(self.false_timeout_s)
        except asyncio.CancelledError:
            return
        if self._candidate:
            logger.info("barge_in.recovered", reason="no_transcript")
            self._resume_playback()

    async def _resolve_candidate(self, transcript: str) -> None:
        if self._candidate_timer:
            self._candidate_timer.cancel()
            self._candidate_timer = None
        verdict = self._classify(transcript)
        if verdict in (Verdict.HARD, Verdict.REAL):
            ms = round((time.perf_counter() - self._candidate_t0) * 1000)
            logger.info("barge_in.confirmed", verdict=verdict.value, decide_ms=ms,
                        text=transcript[:40])
            await self._confirm_interrupt(transcript)
        else:
            logger.info("barge_in.false", verdict=verdict.value, text=transcript[:40])
            self._resume_playback()

    def _resume_playback(self) -> None:
        self._candidate = False
        if self._candidate_timer:
            self._candidate_timer.cancel()
            self._candidate_timer = None
        self._pump_gate.set()  # un-pause; remaining audio keeps playing

    def _classify(self, transcript: str) -> Verdict:
        return classify(
            transcript,
            min_words=self.min_words,
            backchannels=self.backchannels,
            hard_phrases=self.hard_phrases,
        )

    # ─── turn control ───

    def _start_turn(self, transcript: str) -> None:
        self._current_turn = asyncio.create_task(self._do_turn(transcript))

    async def _confirm_interrupt(self, next_transcript: str) -> None:
        """The single, centralised interrupt — same four steps, every time."""
        self._candidate = False
        self.llm.cancel()                       # 1. stop token generation
        turn = self._current_turn
        if turn and not turn.done():
            turn.cancel()                       # 2. unwind the TTS producer
            try:
                await turn
            except asyncio.CancelledError:
                pass
        await asyncio.sleep(0)                   # let the pump settle
        self._flush_playback()                  # 3. drop queued audio (critical!)
        if self._spoken.strip():                # 4. keep only what was heard
            self.history.append({"role": "assistant", "content": self._spoken})
        self._spoken = ""
        self._pump_gate.set()                    # ready to play the next turn
        self.state = State.LISTENING
        self._start_turn(next_transcript)        # the interrupting words are the new turn

    def _flush_playback(self) -> None:
        while not self._playback_queue.empty():
            try:
                self._playback_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    # ─── turns ───

    async def _do_greeting(self) -> None:
        self._spoken = ""
        self.state = State.SPEAKING
        await self._speak(self.greeting_text)
        await self._finish_playback()
        self._spoken = ""
        self.state = State.LISTENING

    async def _do_turn(self, transcript: str) -> None:
        self.state = State.THINKING
        self._spoken = ""
        self.history.append({"role": "user", "content": transcript})
        messages = [{"role": "system", "content": self.system_prompt}] + self.history[-10:]

        if self.enable_fillers and self.filler is not None:
            self.state = State.SPEAKING
            for frame in self._to_frames(self.filler.select(transcript)):
                await self._playback_queue.put(frame)

        sentence_queue: asyncio.Queue = asyncio.Queue()
        llm_task = asyncio.create_task(self.llm.generate_sentences(messages, sentence_queue))
        full = ""
        try:
            while True:
                evt = await sentence_queue.get()
                if evt is None:
                    break
                if isinstance(evt, SentenceEvent):
                    full += evt.text
                    await self._speak(evt.text)
        finally:
            if not llm_task.done():
                self.llm.cancel()
            try:
                await llm_task
            except Exception:
                pass

        await self._finish_playback()
        # On a clean finish, played == generated. (On interrupt we never reach
        # here; _confirm_interrupt commits the played portion instead.)
        if full.strip():
            self.history.append({"role": "assistant", "content": full})
        self._spoken = ""
        self.state = State.LISTENING

    # ─── audio ───

    async def _speak(self, text: str) -> None:
        """Synthesize one chunk of text and stream its frames + a played-mark."""
        await self.tts.reset()
        await self.tts.send_text(text)
        await self.tts.flush()
        emitted = False
        while True:
            audio = await self.tts.get_audio()
            if audio is None:
                break
            if self.state != State.SPEAKING:
                self.state = State.SPEAKING
            for frame in self._to_frames(audio):
                await self._playback_queue.put(frame)
                emitted = True
        if emitted:
            # Pump appends this text to _spoken only after its frames are sent.
            await self._playback_queue.put(_SpokenMark(text))

    async def _finish_playback(self) -> None:
        self._turn_done.clear()
        await self._playback_queue.put(_END)
        await self._turn_done.wait()

    @staticmethod
    def _to_frames(pcm16_8k: bytes) -> list[bytes]:
        if not pcm16_8k:
            return []
        if len(pcm16_8k) % 2:
            pcm16_8k = pcm16_8k[:-1]
        ulaw = audioop.lin2ulaw(pcm16_8k, 2)
        return [ulaw[i:i + FRAME_BYTES] for i in range(0, len(ulaw), FRAME_BYTES)]

    async def _playback_pump(self) -> None:
        """Drain the playback queue to the carrier at real time, pausable.

        Real-time pacing keeps un-played audio in OUR queue so an interrupt's
        flush silences the agent fast; the gate lets a candidate interruption
        pause instantly and resume if it turns out to be a backchannel.
        """
        while True:
            await self._pump_gate.wait()         # blocks while paused
            item = await self._playback_queue.get()
            if item is _END:
                self._turn_done.set()
                continue
            if isinstance(item, _SpokenMark):
                self._spoken += item.text         # this text was actually heard
                continue
            try:
                await self.send_media(item)
            except Exception:
                break
            if self.frame_pace_s:
                await asyncio.sleep(self.frame_pace_s)
