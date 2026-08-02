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

from src.util.logtext import safe as _safe_text

from src.services.stt.sarvam import TranscriptEvent, VADEvent
from src.services.llm.sarvam import SentenceEvent
from src.pipeline.barge_in import classify, Verdict, BACKCHANNELS, HARD_INTERRUPT
from src.observability.metrics import TurnLatency
from src.pipeline.endpointing import looks_continuable
from src.agent.runner import resolve_tools
from src.safety.guard import is_injection, guard_sentence, DEFAULT_REFUSAL
from src.safety.moderation import moderate, is_blocked
from src.pipeline.history import select_context, prune

logger = structlog.get_logger()

# 20ms of mu-law @ 8kHz = 160 bytes.
FRAME_BYTES = 160
FRAME_PACE_S = 0.02

# STT language codes -> names the LLM understands (for the reply-language
# directive). Only languages Sarvam actually detects; unknown codes are ignored
# rather than guessed, so a garbled detection can't flip the reply language.
LANGUAGE_NAMES = {
    "en-IN": "English", "hi-IN": "Hindi", "kn-IN": "Kannada",
    "te-IN": "Telugu", "ta-IN": "Tamil", "ml-IN": "Malayalam",
    "mr-IN": "Marathi", "bn-IN": "Bengali", "gu-IN": "Gujarati",
    "pa-IN": "Punjabi", "od-IN": "Odia",
}

# Unicode script blocks per language, for verifying the LLM actually replied in
# the caller's language (the directive raises compliance but is probabilistic —
# live: one Kannada turn in a long English-heavy call came back in English).
_SCRIPT_BLOCKS = {
    "hi-IN": (0x0900, 0x097F), "mr-IN": (0x0900, 0x097F),
    "bn-IN": (0x0980, 0x09FF), "pa-IN": (0x0A00, 0x0A7F),
    "gu-IN": (0x0A80, 0x0AFF), "od-IN": (0x0B00, 0x0B7F),
    "ta-IN": (0x0B80, 0x0BFF), "te-IN": (0x0C00, 0x0C7F),
    "kn-IN": (0x0C80, 0x0CFF), "ml-IN": (0x0D00, 0x0D7F),
}

_END = object()  # end-of-utterance marker on the playback queue

def _join_dotted_abbreviations(text: str) -> str:
    """Models spell abbreviations with dots ("एच. आर.", "ಯು.ಎಕ್ಸ್.") and TTS
    reads them as halting letter-by-letter fragments. Two conservative fixes,
    Indic-focused (where the problem manifests; Latin is left alone so real
    sentence boundaries like "done. Ok." can't be eaten):
      1. collapse dots INSIDE a dotted token: "ಯು.ಎಕ್ಸ್." -> "ಯುಎಕ್ಸ್"
      2. merge a dotted token with following 1-2-letter dotted Indic tokens:
         "कोकोलेवियोएच. आर. नाम" -> "कोकोलेवियोएचआर नाम"."""
    def _core(w: str) -> str:
        return w.rstrip(".").replace(".", "")

    def _short_indic_dotted(w: str) -> bool:
        c = _core(w)
        return (w.endswith(".") and 1 <= len(c) <= 2
                and all(not ch.isascii() for ch in c if ch.isalpha()))

    toks = text.split(" ")
    out: list[str] = []
    i = 0
    while i < len(toks):
        w = toks[i]
        if w.endswith(".") and i + 1 < len(toks) and _short_indic_dotted(toks[i + 1]):
            merged = _core(w)
            i += 1
            while i < len(toks) and _short_indic_dotted(toks[i]):
                merged += _core(toks[i])
                i += 1
            out.append(merged)
            continue
        if w.endswith(".") and "." in w[:-1] and not w[:-1].replace(".", "").isascii():
            w = _core(w)                      # internal-dot Indic abbreviation
        out.append(w)
        i += 1
    return " ".join(out)


# Greeting-only opener sentences the model prepends to mid-call answers
# ("ನಮಸ್ಕಾರ." before the actual answer) despite persona instructions. They are
# not just filler: a short greeting plays instantly, the real answer is still
# synthesizing, and the silence gap reads as "answer finished" — the caller
# (and the AI tester) asks the next question, which interrupts and KILLS the
# real answer. Live result: "where are you?" answered by "ನಮಸ್ಕಾರ." alone.
_GREETING_WORDS = frozenset({
    "hello", "hi", "hey", "welcome", "greetings", "namaste", "namaskara",
    "namaskar", "ನಮಸ್ಕಾರ", "नमस्ते", "नमस्कार", "నమస్కారం", "నమస్తే",
    "வணக்கம்", "ஹலோ", "ನಮಸ್ತೆ", "हैलो", "हेलो",
})


def _is_greeting_only(text: str) -> bool:
    words = [w.strip(".,!?।॥ ") for w in text.strip().split()]
    words = [w for w in words if w]
    return 0 < len(words) <= 2 and all(w.lower() in _GREETING_WORDS for w in words)


# Human-expression layer (the emotion/non-verbal differentiator). Phase-0 by-ear
# result: neha renders hums, acknowledgment backchannels, ellipsis pauses and
# empathy words NATURALLY from plain text (all languages) — so this is a TEXT
# layer, no audio splicing. Laughter is EXCLUDED: neha reads "haha" as letters,
# and laughter is the highest social-risk non-verbal (misplaced = creepy). The
# CAPS frequency governor + one-feeling rule + hard state bans mirror the
# researched best practice (LLMs overuse tags without an explicit cap).
HUMAN_EXPRESSION_PROMPT = (
    "SOUND HUMAN, NOT ROBOTIC. Where a warm person naturally would, weave short "
    "spoken touches directly into your reply — written IN THE CALLER'S OWN "
    "LANGUAGE so they are said aloud:\n"
    "- Acknowledge/agree: English \"mm-hmm\", \"right\", \"I see\"; Hindi \"हाँ\", "
    "\"अच्छा\", \"जी\", \"ठीक है\"; the natural backchannel in whatever language "
    "the caller uses.\n"
    "- Think / check something: a brief \"hmm\" or \"let me see\", and use \"…\" "
    "for a natural pause while you look it up.\n"
    "- Empathy on a complaint or bad news: a soft \"oh\" and a genuine \"I'm sorry "
    "to hear that\" (in the caller's language).\n"
    "RULES: use these SPARINGLY — AT MOST ONE per reply, and MOST replies should "
    "have NONE. One feeling per turn; do not bounce between emotions. Keep them "
    "short and natural, never forced. NEVER laugh or write \"haha\"/\"hehe\"/"
    "\"lol\" — it sounds fake. NEVER add an acknowledgment or filler when quoting "
    "a price, confirming a booking, or giving critical info — be crisp and clear "
    "there. Never sound cheerful while delivering bad news."
)


# One 20ms frame of mu-law digital silence. Sent whenever we have nothing to
# say: a stream that stops between utterances makes the carrier's comfort-noise
# generator toggle at every sentence edge, which callers hear as soft thumps
# ("pat pat pat") throughout multi-sentence answers.
_SILENCE_FRAME = b"\xff" * FRAME_BYTES


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
        pace_lead_s: float = 0.08,
        sample_rate: int = 8000,
        codec: str = "mulaw",          # "mulaw" (telephony) | "pcm16" (web/app)
        min_words: int = 2,
        false_timeout_s: float = 1.2,
        speech_end_grace_s: float = 0.3,
        enable_recovery: bool = True,
        instant_pause: bool = True,
        enable_smart_endpointing: bool = False,
        continuation_timeout_s: float = 0.6,
        tools=None,
        greeting_audio: bytes | None = None,
        enable_safety: bool = True,
        enable_idle: bool = False,
        idle_reprompt_s: float = 10.0,
        idle_hangup_s: float = 30.0,
        reprompt_text: str = "",
        enable_language_switch: bool = False,
        enable_human_expression: bool = True,
        max_reply_sentences: int = 0,
        knowledge=None,
        router=None,
        on_transcript=None,
        on_metrics=None,
        on_false_recovery=None,
        on_pause=None,
        turn_bucket=None,
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
        # Cushion of audio kept queued at the carrier (absorbs event-loop
        # jitter; costs the same amount of extra tail on a barge-in pause).
        self.pace_lead_s = pace_lead_s
        # Audio format: telephony runs mulaw@8k; the web/app channel runs raw
        # PCM16 at 16k+ (bulbul synthesizes natively at 24k — 8k narrowband
        # throws away most of the voice quality).
        self.sample_rate = sample_rate
        self.codec = codec
        samples_per_frame = sample_rate // 50            # 20ms frames
        self.frame_bytes = samples_per_frame * (1 if codec == "mulaw" else 2)
        self._pcm_frame = samples_per_frame * 2          # PCM16 in per frame
        self._silence_frame = (b"\xff" if codec == "mulaw" else b"\x00") * self.frame_bytes
        # Debug tap: set to a bytearray to capture every frame actually sent
        # (lets a recording of what WE sent be diffed against what arrived).
        self.tap: bytearray | None = None

        # guard-stack tunables
        self.min_words = min_words
        self.false_timeout_s = false_timeout_s
        self.speech_end_grace_s = speech_end_grace_s
        self.enable_recovery = enable_recovery
        # Pause playback the instant VAD fires (pre-transcript). Superb when the
        # audio path is echo-free (web/loopback: 129ms stops), but on PSTN legs
        # with strong uncancelled line echo, false pauses read as "answer done"
        # and get the agent legitimately interrupted mid-answer — measured to
        # truncate EVERY answer on echo-heavy calls. Off -> interrupts confirm
        # via transcript only.
        self.instant_pause = instant_pause
        self.enable_smart_endpointing = enable_smart_endpointing
        self.continuation_timeout_s = continuation_timeout_s
        self.tools = tools                       # ToolRegistry | None
        self.greeting_audio = greeting_audio     # pre-rendered PCM16-8k (instant hello)
        self.enable_safety = enable_safety
        self.enable_idle = enable_idle
        self.idle_reprompt_s = idle_reprompt_s
        self.idle_hangup_s = idle_hangup_s
        self.reprompt_text = reprompt_text
        self.enable_language_switch = enable_language_switch
        self.enable_human_expression = enable_human_expression
        self.max_reply_sentences = max_reply_sentences
        self._caller_language = ""
        self._lang_candidate = ""       # sticky-switch staging (see _track_language)
        self.knowledge = knowledge               # KnowledgeBase | None (RAG)
        self.router = router                     # AgentRouter | None (handoff)
        self.on_transcript = on_transcript       # Callable[[str, str], None] | None
        self.on_metrics = on_metrics             # Callable[[dict], None] | None
        self.on_false_recovery = on_false_recovery  # candidate was echo/noise
        self.on_pause = on_pause                 # flush carrier-side buffered audio
        self.turn_bucket = turn_bucket           # TokenBucket | None (rate limit)
        self.backchannels = backchannels
        self.hard_phrases = hard_phrases

        # Smart-endpointing merge buffer (only used when enabled).
        self._pending_user_text = ""
        self._continuation_timer: asyncio.Task | None = None

        # Idle/silence tracking.
        self._last_activity = 0.0
        self._reprompted = False

        # Deferred post-turn action (e.g. a call transfer scheduled by a tool):
        # runs only AFTER the turn's audio has fully played, so the caller hears
        # the confirmation ("connecting you now") before the action fires.
        self._deferred_action = None

        # Whether the caller is audibly speaking right now (local VAD).
        self._caller_speaking = False

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

        # Latency instrumentation (see src/observability/metrics.py).
        self._turn_seq = 0
        self._last_speech_end: float | None = None
        self._turn_metrics: TurnLatency | None = None

    # ─── lifecycle ───

    async def run(self) -> None:
        self._pump_task = asyncio.create_task(self._playback_pump())
        if self.greeting_text:
            self._current_turn = asyncio.create_task(self._do_greeting())
        try:
            await self._event_loop()
        except asyncio.CancelledError:
            raise
        except Exception:
            # The engine task is created with create_task and its exception is
            # swallowed by the caller's teardown, so a crash here used to make
            # the agent go permanently mute mid-call with NOTHING in the log —
            # indistinguishable from "the caller stopped talking". Never again.
            logger.exception("engine.crashed")
            raise
        finally:
            if self._current_turn and not self._current_turn.done():
                try:
                    await asyncio.wait_for(self._current_turn, timeout=5.0)
                except Exception:
                    pass
            if self._candidate_timer:
                self._candidate_timer.cancel()
            self._cancel_continuation()
            if self._pump_task:
                self._pump_task.cancel()

    async def _event_loop(self) -> None:
        loop = asyncio.get_event_loop()
        self._last_activity = loop.time()
        while True:
            if self.enable_idle:
                # Poll so a silent caller can be re-prompted / hung up on.
                tick = max(0.02, min(1.0, self.idle_reprompt_s or self.idle_hangup_s))
                try:
                    evt = await asyncio.wait_for(self.stt.get_event(), timeout=tick)
                except asyncio.TimeoutError:
                    if self._idle_tick(loop):
                        break          # idle hangup -> end the call
                    continue
            else:
                evt = await self.stt.get_event()
            if evt is None:
                break
            self._last_activity = loop.time()
            self._reprompted = False

            if isinstance(evt, VADEvent):
                if evt.is_speech_start:
                    self._caller_speaking = True
                    # Caller speech onset while we hold the floor -> pause and judge.
                    if (self.instant_pause and self.state == State.SPEAKING
                            and not self._candidate):
                        self._begin_candidate()
                        # True code-side reaction: VAD injected -> pump gated.
                        delta_ms = (time.perf_counter() - evt.timestamp) * 1000
                        if 0 <= delta_ms < 10_000:      # fakes pass timestamp=0
                            logger.info("barge_in.reaction", ms=round(delta_ms, 2))
                else:
                    # END_SPEECH: caller stopped -> anchor for STT-endpoint latency.
                    self._caller_speaking = False
                    self._last_speech_end = evt.timestamp
                    # Force the STT segment to finalize NOW. Sarvam's own
                    # endpointing waits ~1s of silence before emitting the
                    # final; a flush returns it in ~350-450ms (measured), which
                    # cuts both turn latency and barge-in confirmation time.
                    # Safe: the stream survives, a mistimed flush just yields a
                    # fragment, and the client no-ops when nothing is pending.
                    if hasattr(self.stt, "flush"):
                        asyncio.ensure_future(self._flush_stt())
                    # If we're mid-judgement and the caller's blip already ended
                    # with no transcript, it's most likely noise/backchannel —
                    # resume fast instead of sitting silent for false_timeout.
                    if self._candidate:
                        self._on_candidate_speech_end()

            elif isinstance(evt, TranscriptEvent):
                txt = evt.text.strip()
                if not txt:
                    continue
                logger.info("stt.transcript", state=self.state.value,
                            lang=evt.language,
                            text=_safe_text(txt, 80))
                # A failure while handling ONE utterance must not kill the call.
                # These paths tear down the live turn (cancel the LLM, abort the
                # TTS socket, flush audio); if any step raises, the engine loop
                # used to die and the agent went silent for the rest of the
                # call. Log it, drop that utterance, keep listening.
                interrupt_path = self._candidate or self.state in (
                    State.SPEAKING, State.THINKING)
                if interrupt_path:
                    try:
                        if self._candidate:
                            self._track_language(evt.language, txt)
                            await self._resolve_candidate(txt)
                        else:
                            self._track_language(evt.language, txt)
                            verdict = self._classify(txt)
                            if verdict in (Verdict.HARD, Verdict.REAL):
                                await self._confirm_interrupt(txt)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        # Tearing down a live turn touches the LLM, the TTS
                        # socket and the playback queue. If any step raises,
                        # the engine loop used to die and the agent went mute
                        # for the REST of the call. Recover instead.
                        logger.exception("interrupt.failed")
                        self._candidate = False
                        self._pump_gate.set()        # never leave audio paused
                        self.state = State.LISTENING
                    continue
                else:
                    # LISTENING: junk gates BEFORE anything enters history.
                    # Long calls degrade because the context window fills with
                    # backchannels ("Hmm" as a full turn), mic noise, and the
                    # agent's OWN voice echoed back as user turns — by turn ~20
                    # the model is completing a garbage transcript and starts
                    # echoing the caller (seen live: "…what is that?" answered
                    # with " what is that?").
                    if self._is_backchannel_only(txt):
                        logger.info("listening.backchannel_ignored",
                                    text=_safe_text(txt, 30))
                        continue
                    if self._looks_like_own_echo(txt):
                        logger.info("listening.self_echo_dropped",
                                    text=_safe_text(txt, 50))
                        self._notify_false_recovery()   # let the echo profile adapt
                        continue
                    self._track_language(evt.language, txt)
                    self._on_user_final(txt)

    async def _flush_stt(self) -> None:
        try:
            await self.stt.flush()
        except Exception as e:  # noqa: BLE001 — a failed flush must never kill the loop
            logger.warning("stt.flush_failed", error=str(e))

    def _track_language(self, language: str, txt: str) -> None:
        """Sticky language tracking: a one-word blip ("Hmm" labeled te-IN in a
        Kannada call) must not flip the reply language. Switch on a confident
        detection (>=3 words) or on two consecutive sightings."""
        if not language or language == self._caller_language:
            self._lang_candidate = ""
            return
        if (not self._caller_language              # first detection seeds freely
                or len(txt.split()) >= 3 or language == self._lang_candidate):
            self._caller_language = language
            self._lang_candidate = ""
        else:
            self._lang_candidate = language

    def _is_backchannel_only(self, txt: str) -> bool:
        """"Hmm", "ok", "ಸರಿ" while LISTENING: acknowledgement, not a turn."""
        from src.pipeline.barge_in import normalize
        norm = normalize(txt)
        if not norm or len(norm.split()) > 2:
            return False
        return norm in BACKCHANNELS or all(w in BACKCHANNELS for w in norm.split())

    def _is_parrot(self, sentence: str) -> bool:
        """True when a generated sentence is essentially the caller's last
        utterance repeated back (mirror of _looks_like_own_echo, output side)."""
        last_user = next((m["content"] for m in reversed(self.history)
                          if m.get("role") == "user"), "")
        if not last_user:
            return False
        def toks(s: str) -> set:
            return {w.strip(".,?!।॥").lower() for w in s.split()
                    if len(w.strip(".,?!।॥")) > 1}
        a, b = toks(sentence), toks(last_user)
        if len(a) < 2:
            return False
        return len(a & b) / len(a) >= 0.8

    def _is_self_repeat(self, sentence: str) -> bool:
        """True when a generated first sentence essentially repeats one of the
        agent's own recent answers. When the model can't answer a new question
        it falls back to re-reading a canned line — heard live as the same
        'We are a technology consulting company in Austin, Texas' six times,
        which reads as dumb/broken. Same-language only (different scripts share
        no tokens), so legitimate cross-language restatements are unaffected."""
        def toks(s: str) -> set:
            return {w.strip(".,?!।॥").lower() for w in s.split()
                    if len(w.strip(".,?!।॥")) > 2}
        a = toks(sentence)
        if len(a) < 4:
            return False   # short confirmations/greetings may legitimately recur
        prior = [m["content"] for m in self.history if m.get("role") == "assistant"]
        for msg in prior[-3:]:
            b = toks(msg)
            if b and len(a & b) / len(a) >= 0.75:
                return True
        return False

    def _looks_like_own_echo(self, txt: str) -> bool:
        """A 'user' final that is mostly words from the agent's last utterance
        is our own voice leaking back (speakerphone / weak AEC). Answering it
        creates a feedback loop that poisons the history."""
        last = next((m["content"] for m in reversed(self.history)
                     if m.get("role") == "assistant"), "")
        if not last:
            return False
        words = [w.strip(".,?!।॥").lower() for w in txt.split()]
        words = [w for w in words if len(w) > 2]
        if len(words) < 3:
            return False
        tail = last[-400:].lower()
        hits = sum(1 for w in words if w in tail)
        return hits / len(words) >= 0.8

    # ─── candidate interruption (pause -> judge -> resume or confirm) ───

    def _begin_candidate(self) -> None:
        """Pause playback the instant VAD fires. Caller hears silence now; we
        decide whether it sticks once the transcript lands."""
        self._candidate = True
        self._candidate_t0 = time.perf_counter()
        self._pump_gate.clear()  # pump stops before the next frame
        if self.on_pause is not None:
            # Tell the carrier to drop ITS buffered audio too — stopping our
            # sends still leaves ~1-1.6s queued at Telnyx that leaks out after
            # a barge-in (measured on live calls).
            try:
                self.on_pause()
            except Exception:  # noqa: BLE001
                pass
        logger.info("barge_in.candidate", note="paused, awaiting transcript")
        if self.enable_recovery:
            self._candidate_timer = asyncio.create_task(self._candidate_timeout())

    async def _candidate_timeout(self) -> None:
        """False-interruption recovery: VAD fired but no real words arrived.

        NEVER resumes while the caller is still audibly speaking — no matter
        how long they talk. The old bounded re-arm (4 cycles ≈ 5s) gave up on
        long interruptions and the agent leaked speech OVER the caller until
        they stopped (heard live on lengthy questions). Now the timeout clock
        only runs while the caller is QUIET; a 30s hard cap guards the one
        pathological case (VAD stuck 'loud' on constant background noise)."""
        started = time.perf_counter()
        while True:
            try:
                await asyncio.sleep(self.false_timeout_s)
            except asyncio.CancelledError:
                return
            if not self._candidate:
                return
            if self._caller_speaking:
                if time.perf_counter() - started > 30.0:
                    logger.warning("barge_in.recovered", reason="vad_stuck_30s")
                    self._notify_false_recovery()
                    self._resume_playback()
                    return
                continue                      # still talking — never talk over them
            logger.info("barge_in.recovered", reason="no_transcript")
            self._notify_false_recovery()
            self._resume_playback()
            return

    def _notify_false_recovery(self) -> None:
        """A candidate died with no words — likely our own echo; let the audio
        layer recalibrate so it stops fooling us."""
        if self.on_false_recovery is not None:
            try:
                self.on_false_recovery()
            except Exception:  # noqa: BLE001
                pass

    def _on_candidate_speech_end(self) -> None:
        """Caller's speech ended while we're still judging and no transcript has
        landed yet. Swap the long false_timeout for a brief grace: a real (but
        slightly late) transcript still re-interrupts via the SPEAKING-state
        path, while pure noise resumes in ~grace instead of ~false_timeout.
        This is the main anti-stutter lever — without it every cough or breath
        muted the agent for the full false_timeout."""
        if not (self._candidate and self.enable_recovery):
            return
        if self.speech_end_grace_s >= self.false_timeout_s:
            return  # grace wouldn't help; let the original timer run
        if self._candidate_timer:
            self._candidate_timer.cancel()
        self._candidate_timer = asyncio.create_task(self._candidate_grace())

    async def _candidate_grace(self) -> None:
        try:
            await asyncio.sleep(self.speech_end_grace_s)
        except asyncio.CancelledError:
            return
        if self._candidate:
            logger.info("barge_in.recovered", reason="speech_ended_no_transcript")
            self._notify_false_recovery()
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

    def _emit_transcript(self, role: str, text: str) -> None:
        """Push a finalized turn to the optional real-time transcript sink."""
        if self.on_transcript is not None and text:
            try:
                self.on_transcript(role, text)
            except Exception as e:  # noqa: BLE001 — a bad sink must not break the call
                logger.warning("transcript_sink_error", error=str(e))

    def _publish_metrics(self, tl: TurnLatency) -> None:
        """Emit the turn's latency breakdown to the log and the metrics sink.
        Idempotent per turn: the pump publishes at FIRST frame out (the moment
        perceived latency is fully known), so an interrupt or mid-turn hangup
        can no longer lose the measurement; the turn-end call is then a no-op."""
        if tl.emitted:
            return
        b = tl.emit()
        if self.on_metrics is not None and b:
            try:
                self.on_metrics(b)
            except Exception as e:  # noqa: BLE001
                logger.warning("metrics_sink_error", error=str(e))

    # ─── idle / silence handling ───

    def _idle_tick(self, loop) -> bool:
        """Called when no STT event arrived this poll. Returns True to hang up.
        Only counts as idle while we're actually waiting on the caller."""
        if self.state != State.LISTENING or self._candidate or self._pending_user_text:
            self._last_activity = loop.time()        # busy -> not idle
            return False
        idle = loop.time() - self._last_activity
        if idle >= self.idle_hangup_s:
            logger.info("session.idle_hangup", idle_s=round(idle))
            return True
        if self.reprompt_text and not self._reprompted and idle >= self.idle_reprompt_s:
            self._reprompted = True
            logger.info("session.idle_reprompt")
            self._current_turn = asyncio.create_task(self._do_canned(self.reprompt_text))
        return False

    async def _do_canned(self, text: str) -> None:
        """Speak a fixed line (greeting re-prompt) as an interruptible turn."""
        self._spoken = ""
        self.state = State.SPEAKING
        await self._speak(text)
        await self._finish_playback()
        self._spoken = ""
        self.state = State.LISTENING

    async def announce(self, text: str, *, wait_s: float = 6.0) -> bool:
        """Speak an out-of-band line into a live call (e.g. "15 seconds left").

        Driven by an EXTERNAL watchdog, not the turn loop, so it must not cut
        the caller off mid-answer: it waits for the current turn to finish,
        then speaks. Returns False if the call stayed busy the whole window —
        the caller keeps their turn and only the announcement is dropped."""
        deadline = asyncio.get_event_loop().time() + wait_s
        while asyncio.get_event_loop().time() < deadline:
            busy = (self.state != State.LISTENING
                    or (self._current_turn is not None
                        and not self._current_turn.done()))
            if not busy:
                break
            await asyncio.sleep(0.2)
        else:
            logger.info("announce.skipped_busy", text=text[:40])
            return False
        turn = asyncio.create_task(self._do_canned(text))
        self._current_turn = turn
        try:
            await turn
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            return False
        return True

    # ─── smart endpointing (opt-in fragment merge) ───

    def _on_user_final(self, txt: str) -> None:
        """Decide whether a final transcript starts a turn now, or is an
        unfinished fragment we should hold briefly and merge with the next one.
        No-op passthrough when smart endpointing is disabled."""
        if not self.enable_smart_endpointing:
            self._start_turn(txt)
            return
        merged = f"{self._pending_user_text} {txt}".strip() if self._pending_user_text else txt
        if looks_continuable(merged):
            self._pending_user_text = merged
            logger.info("endpoint.hold", text=_safe_text(merged, 60))
            self._arm_continuation()
        else:
            self._pending_user_text = ""
            self._cancel_continuation()
            self._start_turn(merged)

    def _arm_continuation(self) -> None:
        self._cancel_continuation()
        self._continuation_timer = asyncio.create_task(self._continuation_timeout())

    def _cancel_continuation(self) -> None:
        if self._continuation_timer:
            self._continuation_timer.cancel()
            self._continuation_timer = None

    async def _continuation_timeout(self) -> None:
        """The caller didn't continue in time — fire the buffered fragment so we
        never drop input, even if the completeness guess was wrong."""
        try:
            await asyncio.sleep(self.continuation_timeout_s)
        except asyncio.CancelledError:
            return
        txt = self._pending_user_text
        self._pending_user_text = ""
        self._continuation_timer = None
        if txt:
            logger.info("endpoint.fire_on_timeout",
                        text=_safe_text(txt, 60))
            self._start_turn(txt)

    async def _confirm_interrupt(self, next_transcript: str) -> None:
        """The single, centralised interrupt — same four steps, every time."""
        self._candidate = False
        # 0. Silence FIRST. On transcript-confirmed interrupts (no VAD candidate
        # preceded this) the pump is still running — every statement below that
        # awaits would otherwise let the old answer keep playing meanwhile.
        self._pump_gate.clear()
        self.llm.cancel()                       # 1. stop token generation
        turn = self._current_turn
        if turn and not turn.done():
            turn.cancel()                       # 2. unwind the TTS producer
            try:
                # Bounded: wait_for re-cancels on timeout, so a swallowed first
                # cancellation cannot leave the old turn speaking forever.
                await asyncio.wait_for(turn, timeout=1.5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        await asyncio.sleep(0)                   # let the pump settle
        self._flush_playback()                  # 3. drop queued audio (critical!)
        # 3b. Kill the TTS SOCKET. Sarvam has no server-side abort (probed:
        # 'clear'/'cancel' are invalid, a config re-send lets synthesis run to
        # completion) — so after an interrupt the server keeps streaming the
        # OLD answer's audio, which the next turn's collector would pair with
        # the NEW answer's text (heard live: barge 'where are you?' answered
        # with the interrupted services list). A closed socket cannot deliver
        # stale chunks; the reconnect happens under the new turn's LLM time.
        if hasattr(self.tts, "abort"):
            try:
                await self.tts.abort()
            except Exception as e:  # noqa: BLE001
                logger.warning("tts.abort_failed", error=str(e))
        if self.on_pause is not None:            # ...and the carrier's buffer
            try:
                self.on_pause()
            except Exception:  # noqa: BLE001
                pass
        if self._spoken.strip():                # 4. keep only what was heard
            self.history.append({"role": "assistant", "content": self._spoken})
        self._spoken = ""
        if self._turn_metrics is not None:       # keep whatever stages completed
            self._publish_metrics(self._turn_metrics)
            self._turn_metrics = None
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
        self._turn_seq += 1
        tl = TurnLatency(turn_id=self._turn_seq, kind="greeting")
        tl.mark("turn_start")
        tl.mark("llm_first_token")   # greeting text is static — no LLM stage
        self._turn_metrics = tl
        self.state = State.SPEAKING
        if self.greeting_audio:
            # Pre-rendered greeting: the caller hears "hello" instantly instead
            # of paying cold TTS synthesis (~3.5s measured on a real call).
            tl.mark("tts_first_audio")
            self._emit_transcript("assistant", self.greeting_text)
            for frame in self._to_frames(self.greeting_audio):
                await self._playback_queue.put(frame)
            await self._playback_queue.put(_SpokenMark(self.greeting_text))
        else:
            await self._speak(self.greeting_text)
        await self._finish_playback()
        self._publish_metrics(tl)
        if self._turn_metrics is tl:
            self._turn_metrics = None
        self._spoken = ""
        self.state = State.LISTENING

    def defer_after_turn(self, action) -> None:
        """Schedule an async callable to run once the current turn's audio has
        fully played (used by tools like call transfer)."""
        self._deferred_action = action

    async def _run_deferred_action(self) -> None:
        if self._deferred_action is None:
            return
        action, self._deferred_action = self._deferred_action, None
        try:
            await action()
        except Exception as e:  # noqa: BLE001 — a failed action must not kill the loop
            logger.error("deferred_action_failed", error=str(e))

    def _wrong_language(self, text: str) -> bool:
        """True if ``text`` is clearly NOT in the caller's current language.
        Conservative: needs >=4 letters to judge, and mixed content (brand
        names, digits) passes as long as half the letters are in-script."""
        if not (self.enable_language_switch and self._caller_language):
            return False
        letters = [c for c in text if c.isalpha()]
        if len(letters) < 4:
            return False
        block = _SCRIPT_BLOCKS.get(self._caller_language)
        if block is None:                        # English (or unknown): expect ASCII
            if self._caller_language in LANGUAGE_NAMES:
                share = sum(c.isascii() for c in letters) / len(letters)
                return share < 0.5
            return False
        share = sum(block[0] <= ord(c) <= block[1] for c in letters) / len(letters)
        return share < 0.5

    async def _maybe_switch_language(self) -> None:
        """Reconnect TTS to the caller's language if it changed (no-op otherwise)."""
        if (self.enable_language_switch and self._caller_language
                and hasattr(self.tts, "ensure_language")):
            try:
                await self.tts.ensure_language(self._caller_language)
            except Exception as e:  # noqa: BLE001
                logger.warning("tts.language_switch_failed", error=str(e))

    def _guard(self, text: str, active_prompt: str) -> tuple[str, bool]:
        """Safety-check a spoken chunk (prompt-leak + moderation). Returns
        (text_to_speak, blocked)."""
        if not self.enable_safety:
            return text, False
        # Leak guard fires ONLY on turns where the caller attempted prompt
        # extraction — never on a normal turn (an agent describing its own
        # company/services quotes its prompt legitimately).
        if getattr(self, "_injection_turn", False):
            guarded, blocked = guard_sentence(text, active_prompt)
            if blocked:
                return guarded, True
        cats = moderate(text)
        if is_blocked(cats):
            return DEFAULT_REFUSAL, True
        if cats:
            logger.info("safety.flagged", categories=sorted(cats))
        return text, False

    async def _stream_answer(self, messages: list[dict], tl: TurnLatency,
                             active_prompt: str) -> str:
        """Stream the LLM answer into TTS/playback with PIPELINED synthesis.

        Sentence N+1's text is sent to TTS while sentence N's audio is still
        arriving/playing — synthesis (~0.6s) finishes well inside playback time
        (~2-3s), so the caller hears one continuous answer. The serial version
        waited out each sentence's full audio (plus a completion timeout)
        before even STARTING the next synthesis, which put 1-3s of dead air
        between sentences on live calls ("We are based in Austin, …… Texas.")."""
        sentence_queue: asyncio.Queue = asyncio.Queue()
        tl.mark("turn_start")
        llm_task = asyncio.create_task(self.llm.generate_sentences(messages, sentence_queue))
        # Overlap the TTS language switch with LLM generation (hidden under TTFT).
        await self._maybe_switch_language()

        pending: asyncio.Queue = asyncio.Queue()   # texts awaiting their audio
        collector = asyncio.create_task(self._collect_tts_audio(pending))
        await self.tts.reset()
        full = ""
        first = True
        retries = 0
        spoken = 0                                 # sentences actually fed to TTS
        try:
            while True:
                evt = await sentence_queue.get()
                if evt is None:
                    break
                if isinstance(evt, SentenceEvent):
                    tl.mark("llm_first_token")
                    # Language guard: verify the FIRST sentence is in the
                    # caller's language BEFORE any of it reaches TTS. Retry 1:
                    # blunt corrective appended. Retry 2: strip conversation
                    # history entirely — several turns of another language beat
                    # a corrective (seen live: English question after three
                    # Kannada exchanges got Kannada twice), but system+question
                    # alone follows the directive reliably.
                    if first and retries < 2 and self._wrong_language(evt.text):
                        lang = LANGUAGE_NAMES.get(self._caller_language, "")
                        logger.warning("language.guard_retry", want=lang, n=retries + 1,
                                       got=_safe_text(evt.text, 40))
                        self.llm.cancel()
                        llm_task.cancel()
                        try:
                            await llm_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        sentence_queue = asyncio.Queue()
                        corrective = {
                            "role": "system",
                            "content": (f"STOP. That reply was not in {lang}. "
                                        f"Rewrite your ENTIRE answer strictly in "
                                        f"{lang} (native script). Do not use any "
                                        f"other language.")}
                        if retries == 0:
                            messages = messages + [corrective]
                        else:
                            last_user = next((m for m in reversed(messages)
                                              if m["role"] == "user"), None)
                            messages = [messages[0]] + \
                                ([last_user] if last_user else []) + [corrective]
                        llm_task = asyncio.create_task(
                            self.llm.generate_sentences(messages, sentence_queue))
                        retries += 1
                        continue
                    # Parrot guard: with garbled/fragment inputs the model
                    # sometimes ECHOES the caller's question instead of
                    # answering (heard live, twice in one call). One corrective
                    # regenerate; nothing parroted is ever spoken.
                    if (first and retries < 2
                            and self._is_parrot(evt.text)):
                        logger.warning("parrot.guard_retry",
                                       got=_safe_text(evt.text, 40))
                        self.llm.cancel()
                        llm_task.cancel()
                        try:
                            await llm_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        sentence_queue = asyncio.Queue()
                        messages = messages + [{
                            "role": "system",
                            "content": ("Do NOT repeat the caller's words back. "
                                        "ANSWER the question directly; if it is "
                                        "unclear, politely ask them to repeat it.")}]
                        llm_task = asyncio.create_task(
                            self.llm.generate_sentences(messages, sentence_queue))
                        retries += 1
                        continue
                    # Self-repeat guard: the model, stuck on a question it can't
                    # answer from its FACTS, re-reads a canned line verbatim
                    # (heard live: same "Austin, Texas" description six times).
                    # One corrective regenerate that forbids the repeat and gives
                    # a graceful escape hatch (redirect / offer to help) so it
                    # does not instead hallucinate to fill the gap.
                    if (first and retries < 2
                            and self._is_self_repeat(evt.text)):
                        logger.warning("self_repeat.guard_retry",
                                       got=_safe_text(evt.text, 40))
                        self.llm.cancel()
                        llm_task.cancel()
                        try:
                            await llm_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        sentence_queue = asyncio.Queue()
                        messages = messages + [{
                            "role": "system",
                            "content": ("You ALREADY gave that exact answer. Do NOT "
                                        "repeat a previous reply. Respond to the "
                                        "caller's LATEST message with NEW, specific "
                                        "wording. If it asks something outside what "
                                        "you know, say so in one short sentence and "
                                        "offer to help or connect them — never "
                                        "restate the same description, and never "
                                        "invent facts.")}]
                        llm_task = asyncio.create_task(
                            self.llm.generate_sentences(messages, sentence_queue))
                        retries += 1
                        continue
                    # Drop greeting-only openers ("ನಮಸ್ಕಾರ.") — the call was
                    # already greeted; mid-call they create a fake end-of-answer
                    # gap that gets the real answer interrupted. `first` stays
                    # True so the REAL first sentence keeps clause-flush and
                    # language-guard treatment.
                    if first and _is_greeting_only(evt.text):
                        logger.info("greeting_opener_suppressed",
                                    text=_safe_text(evt.text, 30))
                        continue
                    first = False
                    text, blocked = self._guard(evt.text, active_prompt)
                    if blocked:
                        logger.warning("safety.output_blocked")
                        full = text
                        await self._feed_tts(text, pending)
                        self.llm.cancel()
                        break
                    full += evt.text
                    await self._feed_tts(evt.text, pending)
                    spoken += 1
                    # Hard sentence cap: a deterministic backstop for models
                    # that ignore "1-2 sentences" prompt rules (heard live: a
                    # five-sentence identity ramble). The unspoken tail never
                    # reaches TTS OR history — `full` holds only what was said.
                    if self.max_reply_sentences and spoken >= self.max_reply_sentences:
                        logger.info("reply.sentence_cap",
                                    cap=self.max_reply_sentences)
                        self.llm.cancel()
                        break
            await pending.put(None)                # no more sentences
            await collector                        # wait for the audio to finish
        except asyncio.CancelledError:
            self.llm.cancel()
            llm_task.cancel()
            collector.cancel()
            for t in (llm_task, collector):
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            raise
        finally:
            if not llm_task.done():
                self.llm.cancel()
            try:
                await llm_task
            except (asyncio.CancelledError, Exception):
                pass
            if not collector.done():
                collector.cancel()
                try:
                    await collector
                except (asyncio.CancelledError, Exception):
                    pass
        return full

    async def _feed_tts(self, text: str, pending: asyncio.Queue) -> None:
        """Send one sentence to TTS immediately (no waiting for prior audio)."""
        text = _join_dotted_abbreviations(text)
        self._emit_transcript("assistant", text)
        await self.tts.send_text(text)
        await self.tts.flush()
        await pending.put(text)

    async def _collect_tts_audio(self, pending: asyncio.Queue) -> None:
        """Drain synthesized audio in order: for each pending sentence, queue
        its frames and then its played-mark. Runs concurrently with feeding."""
        while True:
            text = await pending.get()
            if text is None:
                return
            emitted = False
            carry = b""     # partial-frame PCM carried ACROSS chunks: framing
                            # each chunk separately injected a click ("pat pat
                            # pat") at every chunk boundary on live calls.
            while True:
                try:
                    audio = await asyncio.wait_for(
                        self.tts.get_audio(), timeout=2.0 if emitted else 10.0)
                except asyncio.TimeoutError:
                    if asyncio.current_task().cancelling():
                        raise asyncio.CancelledError()
                    logger.info("tts.completion_by_timeout", emitted=emitted)
                    break
                if audio is None:
                    break
                if self._turn_metrics is not None:
                    self._turn_metrics.mark("tts_first_audio")
                if self.state != State.SPEAKING:
                    self.state = State.SPEAKING
                data = carry + audio
                usable = (len(data) // self._pcm_frame) * self._pcm_frame
                carry = data[usable:]
                for frame in self._to_frames(data[:usable]):
                    await self._playback_queue.put(frame)
                    emitted = True
            if carry:                        # utterance tail: pad ONCE, at the end
                for frame in self._to_frames(carry):
                    await self._playback_queue.put(frame)
                    emitted = True
            if emitted:
                await self._playback_queue.put(_SpokenMark(text))

    async def _do_turn(self, transcript: str) -> None:
        if self.turn_bucket is not None and not self.turn_bucket.allow(asyncio.get_event_loop().time()):
            logger.warning("ratelimit.turn_dropped",
                           text=_safe_text(transcript, 40))
            self.state = State.LISTENING
            return
        self.state = State.THINKING
        self._spoken = ""
        self._turn_seq += 1
        tl = TurnLatency(turn_id=self._turn_seq)
        tl.user_speech_end = self._last_speech_end   # may be None (text-driven turn)
        tl.mark("transcript_in")
        self._last_speech_end = None
        self._turn_metrics = tl
        logger.info("turn.start", user_text=_safe_text(transcript, 80))
        # The prompt-leak guard only ARMS when the caller actually tried to
        # extract the prompt this turn. Otherwise a legitimate self-description
        # ("what does your company do") gets refused as a "leak" (heard live).
        self._injection_turn = self.enable_safety and is_injection(transcript)
        if self._injection_turn:
            logger.warning("safety.injection_flagged",
                           text=_safe_text(transcript, 60))
        self.history.append({"role": "user", "content": transcript})
        self._emit_transcript("user", transcript)

        # Multi-agent routing: pick the active persona/tools for this turn.
        active_prompt = self.system_prompt
        active_tools = self.tools
        if self.router is not None:
            agent = self.router.route(transcript)
            active_prompt = agent.system_prompt or self.system_prompt
            if agent.tools is not None:
                active_tools = agent.tools
            logger.info("agent.routed", agent=agent.name)

        # The model's internal sense of "today" is its training cutoff — asked
        # the date on a live call it answered a year in the past. The server
        # always knows; tell it every turn (IST — our callers' timezone).
        now_ist = time.strftime("%A, %d %B %Y, %I:%M %p",
                                time.gmtime(time.time() + 19800))
        sys_content = f"Current date and time (IST): {now_ist}.\n\n{active_prompt}"
        if self.enable_human_expression:
            sys_content += f"\n\n{HUMAN_EXPRESSION_PROMPT}"
        if self.knowledge is not None:
            snippets = self.knowledge.retrieve(transcript)
            # ALWAYS ground the model in the first doc (company identity) PLUS
            # whatever matched the question: garbled STT can miss retrieval and
            # an ungrounded LLM invents fake companies (seen live: "VocalView").
            if self.knowledge.docs:
                identity = self.knowledge.docs[0]
                # No hard cap beyond what retrieve() returns: the cross-script
                # fallback deliberately returns the whole (budgeted) KB.
                snippets = [identity] + [s for s in snippets if s != identity]
            if snippets:
                sys_content += ("\n\nFACTS (answer from these — never invent or "
                                "contradict them):\n- " + "\n- ".join(snippets))
                logger.info("rag.injected", n=len(snippets))
        # Deterministic live-data prefetch: the SERVER detects price/current-
        # affairs intent (any language) and fetches the facts itself — the
        # model receives real numbers instead of choosing whether to call a
        # tool. Model-driven selection failed live (sarvam-30b under long
        # multilingual context narrated instead of calling, then hallucinated
        # a gold price 4x off). The previous user turn joins the probe so
        # follow-ups like "22." inherit the intent.
        if self.tools is not None and hasattr(self.tools, "prefetch"):
            prev_user = next((m["content"] for m in reversed(self.history[:-1])
                              if m.get("role") == "user"), "")
            probe = f"{prev_user}\n{transcript}"
            # If the tools know this turn needs a SLOW live lookup (web search),
            # cover its latency by speaking a short filler CONCURRENTLY with the
            # search — otherwise the caller hears ~1-2s of dead air. The filler
            # is ephemeral (never enters history). Uses the same safe TTS path
            # as the greeting, so audio format/framing is identical.
            hint = (self.tools.filler_hint(transcript)
                    if hasattr(self.tools, "filler_hint") else None)
            if hint:
                self.state = State.SPEAKING
                prefetch_task = asyncio.create_task(self.tools.prefetch(probe))
                try:
                    await self._speak(hint, ephemeral=True)
                    live = await prefetch_task
                except asyncio.CancelledError:
                    prefetch_task.cancel()
                    raise
            else:
                live = await self.tools.prefetch(probe)
            if live:
                sys_content += ("\n\nLIVE DATA fetched right now — answer with "
                                "these EXACT figures and facts, never estimate:"
                                "\n- " + "\n- ".join(live))
                logger.info("prefetch.injected", n=len(live))

        messages = [{"role": "system", "content": sys_content}] + select_context(self.history)

        if self.enable_fillers and self.filler is not None:
            self.state = State.SPEAKING
            for frame in self._to_frames(self.filler.select(transcript)):
                await self._playback_queue.put(frame)

        # Tool/function-calling: the terminating completion IS the answer, so we
        # speak it directly instead of making a SECOND (streaming) LLM call —
        # eliminating a full round-trip per tool-enabled turn. TTS streams the
        # audio, so first-audio latency is unchanged. NOTE: tools run WITHOUT
        # the language directive — a trailing system message after the user
        # turn makes Sarvam models skip tool selection entirely and answer (or
        # refuse) directly (proven offline: 1 tool call without it, 0 with).
        tool_answer = None
        if active_tools is not None and len(active_tools):
            tl.mark("turn_start")
            messages, tool_answer = await resolve_tools(self.llm.complete, messages, active_tools)

        # Mirror the caller's language. TTS reconnection alone is not enough —
        # the LLM keeps answering in the prompt's language unless told, so a
        # caller who switches to Kannada mid-call would hear Kannada-accented
        # English. The directive goes LAST: buried in a long English system
        # prompt with English history, the model follows conversation momentum
        # and ignores it (seen live); recency makes it stick.
        if self.enable_language_switch and self._caller_language:
            lang = LANGUAGE_NAMES.get(self._caller_language, "")
            if lang:
                messages.append({
                    "role": "system",
                    "content": (f"The caller is now speaking {lang}. Reply ONLY "
                                f"in {lang} (native script), even though the "
                                f"instructions, facts and earlier turns are in "
                                f"another language.")})
                logger.info("language.directive", lang=lang)

        # A tool answer generated without the directive may be in the wrong
        # language — if so, regenerate through the streaming path, which has
        # the directive AND the language guard (tool results stay in messages).
        if tool_answer and self._wrong_language(tool_answer):
            logger.info("tool_answer.wrong_language_restream")
            tool_answer = None

        if tool_answer:
            await self._maybe_switch_language()
            tl.mark("llm_first_token")
            full, _blocked = self._guard(tool_answer, active_prompt)
            if full.strip():
                await self._speak(full)
        else:
            full = await self._stream_answer(messages, tl, active_prompt)

        await self._finish_playback()
        await self._run_deferred_action()   # e.g. transfer, after audio played
        # History records what the caller HEARD (pump-confirmed marks), not what
        # the LLM generated: a failed tail synthesis means `full` contains
        # sentences that were never spoken, and the model would believe it said
        # them. Fall back to `full` only when no mark landed at all.
        said = self._spoken if self._spoken.strip() else full
        if said.strip():
            self.history.append({"role": "assistant", "content": said})
            logger.info("turn.done", assistant_text=_safe_text(said, 100))
        self.history = prune(self.history)
        self._publish_metrics(tl)
        if self._turn_metrics is tl:
            self._turn_metrics = None
        self._spoken = ""
        self.state = State.LISTENING

    # ─── audio ───

    async def _speak(self, text: str, *, ephemeral: bool = False) -> None:
        """Synthesize one chunk of text and stream its frames + a played-mark.

        Completion is detected by the provider's completion event OR by a
        sustained gap after the last audio chunk. Live calls showed Sarvam's
        completion event sometimes never arrives — without the gap fallback the
        turn hung inside this loop forever and every answer was truncated by
        the caller's next question.

        ``ephemeral=True`` speaks a throwaway line (a search filler) that must
        NOT enter history: it emits no _SpokenMark, so the pump never appends it
        to ``_spoken`` and the model never believes it 'said' the filler."""
        # Real-time transcript feed: emit each sentence as it heads to TTS so
        # live viewers and the voice tester see the answer as it is spoken.
        # (History still records only what actually PLAYED — different concern.)
        self._emit_transcript("assistant", text)
        await self.tts.reset()
        await self.tts.send_text(text)
        await self.tts.flush()
        emitted = False
        carry = b""     # partial frame carried ACROSS chunks — framing each
        #                 chunk separately padded ~15 silence slivers into
        #                 every answer (THE pat-pat-pat; this path serves
        #                 tool-enabled agents, which is why only they had it)
        while True:
            try:
                # 10s for synthesis to start; 2s inter-chunk gap = done.
                audio = await asyncio.wait_for(
                    self.tts.get_audio(), timeout=2.0 if emitted else 10.0)
            except asyncio.TimeoutError:
                # wait_for can convert a concurrent task-cancellation into
                # TimeoutError; swallowing it would UN-CANCEL the turn and let
                # it keep speaking after a confirmed interrupt. Re-raise.
                if asyncio.current_task().cancelling():
                    raise asyncio.CancelledError()
                logger.info("tts.completion_by_timeout", emitted=emitted)
                break
            if audio is None:
                break
            if self._turn_metrics is not None:
                self._turn_metrics.mark("tts_first_audio")
            if self.state != State.SPEAKING:
                self.state = State.SPEAKING
            data = carry + audio
            usable = (len(data) // self._pcm_frame) * self._pcm_frame
            carry = data[usable:]
            for frame in self._to_frames(data[:usable]):
                await self._playback_queue.put(frame)
                emitted = True
        if carry:                        # utterance tail: pad ONCE, at the end
            for frame in self._to_frames(carry):
                await self._playback_queue.put(frame)
                emitted = True
        if emitted and not ephemeral:
            # Pump appends this text to _spoken only after its frames are sent.
            await self._playback_queue.put(_SpokenMark(text))

    async def _finish_playback(self) -> None:
        self._turn_done.clear()
        await self._playback_queue.put(_END)
        await self._turn_done.wait()

    def _to_frames(self, pcm16: bytes) -> list[bytes]:
        if not pcm16:
            return []
        if len(pcm16) % 2:
            pcm16 = pcm16[:-1]
        if self.codec == "mulaw":
            data, pad = audioop.lin2ulaw(pcm16, 2), b"\xff"
        else:                                  # raw PCM16 (web/app channel)
            data, pad = pcm16, b"\x00"
        fb = self.frame_bytes
        frames = [data[i:i + fb] for i in range(0, len(data), fb)]
        # Never emit a short frame: carriers glitch on it (audible click).
        if frames and len(frames[-1]) < fb:
            frames[-1] += pad * (fb - len(frames[-1]))
        return frames

    async def _playback_pump(self) -> None:
        """Drain the playback queue to the carrier at real time, pausable.

        Real-time pacing keeps un-played audio in OUR queue so an interrupt's
        flush silences the agent fast; the gate lets a candidate interruption
        pause instantly and resume if it turns out to be a backchannel.

        Pacing chases an ABSOLUTE per-frame deadline (``loop.time() + N*pace``)
        instead of sleeping ``frame_pace_s`` after every send. A fixed per-frame
        sleep folds the send + scheduling overhead into audio drift, and on a
        coarse-timer platform (Windows' ~15ms granularity) a 20ms frame paces at
        ~31ms — 65% of real time — starving the carrier and making speech choppy.
        Deadline-chasing self-corrects: when the clock runs coarse, frames whose
        deadline already passed flush back-to-back to catch up, so the average
        rate holds at real time everywhere. A long stall (>200ms, i.e. a barge-in
        pause) re-anchors the clock so resume doesn't replay a sped-up burst.
        """
        loop = asyncio.get_event_loop()
        next_send: float | None = None
        late_frames = 0
        underruns = 0                # queue ran dry MID-sentence (TTS starving us)
        mid_sentence = False
        gate = self._pump_gate
        while True:
            if not gate.is_set():                # only await when actually paused
                await gate.wait()
            is_fill = False
            if self.frame_pace_s:
                try:
                    item = self._playback_queue.get_nowait()
                except asyncio.QueueEmpty:
                    if mid_sentence:
                        # NEVER inject silence INSIDE speech: a late TTS chunk
                        # with silence stuffed before it is heard as a break in
                        # the word. Wait for the audio; the deadline pacer's
                        # catch-up absorbs the slip. (Silence-fill mid-speech
                        # was itself heard live as "small gaps / voice breaks".)
                        underruns += 1
                        item = await self._playback_queue.get()
                    else:
                        item = self._silence_frame   # keep the RTP stream warm
                        is_fill = True
            else:
                item = await self._playback_queue.get()
            if item is _END:
                self._turn_done.set()
                next_send = None                 # reset the clock between turns
                if late_frames or underruns:
                    logger.info("pump.stats", late=late_frames, underruns=underruns)
                    late_frames = underruns = 0
                mid_sentence = False
                continue
            if isinstance(item, _SpokenMark):
                self._spoken += item.text         # this text was actually heard
                mid_sentence = False              # sentence boundary: a wait is OK
                continue
            if not is_fill:
                mid_sentence = True
            if self.frame_pace_s:
                now = loop.time()
                # Re-anchor when behind by >80ms instead of burst-flooding to
                # catch up: bursts pile up in the carrier's downstream buffer
                # and LEAK OUT as 1-2s of voice after a barge-in pause. A small
                # timeline slip is inaudible; a post-interrupt leak is not.
                if next_send is None or now - next_send > 0.08:
                    next_send = now              # fresh turn / stall / post-pause
                # Send each frame PACE_LEAD_S ahead of its deadline so the
                # carrier holds a small cushion. Exact-deadline pacing has zero
                # slack: every event-loop stall >20ms (Windows' coarse timer,
                # DSP/STT work sharing the loop) starved the carrier and played
                # out as mid-word micro-gaps — the "loose mic wire" patting,
                # denser as calls age and per-turn work grows.
                delay = next_send - self.pace_lead_s - now
                if delay > 0:
                    await asyncio.sleep(delay)
                elif delay < -0.04:
                    late_frames += 1             # >40ms late = audible risk
                next_send += self.frame_pace_s
            if self._turn_metrics is not None and not is_fill:
                tm = self._turn_metrics
                tm.mark("first_frame_out")    # first audio the caller hears
                if not tm.emitted:
                    self._publish_metrics(tm)  # perceived_ms is known NOW
            try:
                await self.send_media(item)
                if self.tap is not None:
                    self.tap += item             # debug: exact bytes we sent
            except Exception as e:
                logger.warning("pump.send_failed", error=str(e))
                break
