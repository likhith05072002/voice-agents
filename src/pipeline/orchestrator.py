"""Voice Session Orchestrator — wires STT, LLM, TTS into a real-time pipeline.

Each instance manages one active phone call. Created on call start, destroyed on hangup.

State machine:
  IDLE -> LISTENING -> PROCESSING -> SPEAKING -> LISTENING -> ...
  At any point during SPEAKING: barge-in -> cancel TTS -> LISTENING

Critical path (measured): LLM TTFT (~346ms) + TTS TTFA (~290ms) = ~636ms
With filler audio, perceived latency is ~250ms.
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum

import structlog

from src.services.stt.sarvam import SarvamSTTClient, TranscriptEvent, VADEvent
from src.services.llm.sarvam import SarvamLLMClient, SentenceEvent
from src.services.tts.sarvam import SarvamTTSClient

logger = structlog.get_logger()


class SessionState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"


@dataclass
class TurnMetrics:
    turn_start: float = 0.0
    stt_final: float = 0.0
    llm_first_token: float = 0.0
    llm_first_sentence: float = 0.0
    tts_first_audio: float = 0.0
    turn_end: float = 0.0

    def log(self, turn_num: int) -> None:
        if not all([self.turn_start, self.stt_final, self.tts_first_audio]):
            return
        e2e = (self.tts_first_audio - self.stt_final) * 1000
        llm = (self.llm_first_sentence - self.stt_final) * 1000 if self.llm_first_sentence else -1
        tts = (self.tts_first_audio - self.llm_first_sentence) * 1000 if self.llm_first_sentence else -1
        logger.info(
            "turn.metrics",
            turn=turn_num,
            e2e_ms=round(e2e),
            llm_sentence_ms=round(llm),
            tts_ttfa_ms=round(tts),
        )


@dataclass
class VoiceSession:
    session_id: str
    language: str = "te-IN"
    voice: str = "anushka"
    system_prompt: str = ""
    state: SessionState = SessionState.IDLE
    conversation_history: list = field(default_factory=list)
    turn_count: int = 0

    # Clients (set during init)
    stt: SarvamSTTClient | None = None
    llm: SarvamLLMClient | None = None
    tts: SarvamTTSClient | None = None

    # Audio output callback
    on_audio_out: asyncio.Queue | None = None  # bytes queue -> telephony


class PipelineOrchestrator:
    """Manages the STT -> LLM -> TTS pipeline for one call."""

    def __init__(self, session: VoiceSession, api_key: str):
        self.session = session
        self.api_key = api_key
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """Initialize all service connections and start pipeline tasks."""
        s = self.session

        # Create clients
        s.stt = SarvamSTTClient(self.api_key)
        s.llm = SarvamLLMClient(self.api_key)
        s.tts = SarvamTTSClient(self.api_key)
        s.on_audio_out = asyncio.Queue()

        # Connect STT and TTS (persistent for call duration)
        await s.stt.connect(language=s.language)
        await s.tts.connect(language=s.language, voice=s.voice, sample_rate="8000")

        s.state = SessionState.LISTENING
        logger.info("pipeline.started", session_id=s.session_id)

    async def feed_audio(self, pcm_16k: bytes) -> None:
        """Feed audio from telephony to STT. Called for each audio chunk."""
        if self.session.stt:
            await self.session.stt.send_audio(pcm_16k)

    async def process_stt_events(self) -> None:
        """Main loop: consume STT events, trigger LLM+TTS on turn completion.
        Run this as an asyncio task for the duration of the call."""
        s = self.session
        current_transcript = ""
        vad_speech_ended = False
        metrics = TurnMetrics()

        while True:
            evt = await s.stt.get_event()
            if evt is None:
                break  # STT connection closed

            if isinstance(evt, VADEvent):
                if not evt.is_speech_start:
                    # Speech ended — mark for turn detection
                    vad_speech_ended = True
                else:
                    # New speech started
                    vad_speech_ended = False
                    if s.state == SessionState.SPEAKING:
                        # Barge-in: user started talking while bot is speaking
                        await self._handle_barge_in()
                    if s.state != SessionState.LISTENING:
                        s.state = SessionState.LISTENING
                    metrics = TurnMetrics(turn_start=time.perf_counter())

            elif isinstance(evt, TranscriptEvent):
                if evt.text.strip():
                    current_transcript = evt.text.strip()

                    # Turn complete: we have transcript + VAD says speech ended
                    if vad_speech_ended and current_transcript:
                        metrics.stt_final = time.perf_counter()
                        s.turn_count += 1
                        logger.info(
                            "turn.detected",
                            turn=s.turn_count,
                            transcript=current_transcript[:80],
                        )

                        # Process the turn
                        await self._process_turn(current_transcript, metrics)
                        current_transcript = ""
                        vad_speech_ended = False
                        metrics = TurnMetrics()

    async def _process_turn(self, transcript: str, metrics: TurnMetrics) -> None:
        """Handle a complete user turn: overlapping LLM -> TTS pipeline.

        Optimization: Send text to TTS as soon as we have 15 chars from LLM,
        don't wait for a full sentence. TTS starts synthesizing while LLM
        continues generating. This saves ~100-200ms vs sequential approach.
        """
        s = self.session
        s.state = SessionState.PROCESSING

        # Add to conversation history
        s.conversation_history.append({"role": "user", "content": transcript})

        # Build LLM messages
        messages = [{"role": "system", "content": s.system_prompt}]
        messages.extend(s.conversation_history[-10:])

        # Start LLM streaming with sentence detection
        sentence_queue = asyncio.Queue()
        llm_task = asyncio.create_task(s.llm.generate_sentences(messages, sentence_queue))

        full_response = ""
        first_tts_send = True

        while True:
            evt = await sentence_queue.get()
            if evt is None:
                break

            if isinstance(evt, SentenceEvent):
                if first_tts_send:
                    metrics.llm_first_sentence = evt.timestamp
                    first_tts_send = False

                full_response += evt.text
                s.state = SessionState.SPEAKING

                # Send to TTS and stream audio to telephony
                await s.tts.send_text(evt.text)
                await s.tts.flush()

                while True:
                    audio = await s.tts.get_audio()
                    if audio is None:
                        break
                    if metrics.tts_first_audio == 0.0:
                        metrics.tts_first_audio = time.perf_counter()
                    await s.on_audio_out.put(audio)

                await s.tts.reset()

        if full_response:
            s.conversation_history.append({"role": "assistant", "content": full_response})

        metrics.turn_end = time.perf_counter()
        metrics.log(s.turn_count)
        s.state = SessionState.LISTENING

        try:
            await llm_task
        except Exception:
            pass

    async def _handle_barge_in(self) -> None:
        """Cancel current TTS playback when user interrupts."""
        s = self.session
        logger.info("barge_in.triggered", turn=s.turn_count)

        # Cancel LLM
        if s.llm:
            s.llm.cancel()

        # Drain TTS audio queue
        if s.tts:
            while not s.tts._audio_queue.empty():
                try:
                    s.tts._audio_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

        # Drain output audio queue
        if s.on_audio_out:
            while not s.on_audio_out.empty():
                try:
                    s.on_audio_out.get_nowait()
                except asyncio.QueueEmpty:
                    break

        s.state = SessionState.LISTENING

    async def stop(self) -> None:
        """Cleanup on call end."""
        s = self.session
        for task in self._tasks:
            task.cancel()

        if s.stt:
            await s.stt.close()
        if s.tts:
            await s.tts.close()
        if s.llm:
            await s.llm.close()

        s.state = SessionState.IDLE
        logger.info(
            "pipeline.stopped",
            session_id=s.session_id,
            turns=s.turn_count,
        )
