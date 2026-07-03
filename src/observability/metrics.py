"""Per-turn latency instrumentation for the cascaded voice pipeline.

The number that decides whether the agent *feels* fast is the wall-clock gap
between the caller finishing their utterance and the first reply audio reaching
the carrier — "perceived latency". That number is otherwise invisible: the
service clients log LLM and TTS timings in isolation, but nothing measures the
end-to-end turn, nor the STT endpointing gap (caller-stops -> transcript) which
is usually the single largest hidden cost on a cascaded stack.

``TurnLatency`` records monotonic timestamps at each stage boundary and emits one
structured ``turn.latency`` event with the full breakdown, so every turn is
measurable in production without a debugger. It is deliberately tiny and
allocation-light (one dataclass per turn) so it never perturbs the path it
measures.

Stage map (all optional; a stage left None simply drops out of the breakdown)::

    user_speech_end ──stt_endpoint──▶ transcript_in ──queue──▶ turn_start
        ──llm_ttft──▶ llm_first_token ──tts_ttfa──▶ tts_first_audio
        ──tts_to_frame──▶ first_frame_out

    perceived_ms = first_frame_out − user_speech_end   (the headline metric)
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()


def now() -> float:
    """Monotonic clock used for all latency math (never wall-clock)."""
    return time.perf_counter()


# Ordered stage fields -> the segment label that ends AT that stage.
_SEGMENTS: tuple[tuple[str, str], ...] = (
    ("user_speech_end", "transcript_in", "stt_endpoint_ms"),
    ("transcript_in", "turn_start", "queue_ms"),
    ("turn_start", "llm_first_token", "llm_ttft_ms"),
    ("llm_first_token", "tts_first_audio", "tts_ttfa_ms"),
    ("tts_first_audio", "first_frame_out", "tts_to_frame_ms"),
)


@dataclass
class TurnLatency:
    """Mutable stage-timestamp record for a single turn."""

    turn_id: int
    kind: str = "turn"                      # "turn" | "greeting"
    user_speech_end: float | None = None    # VAD END_SPEECH (caller stopped)
    transcript_in: float | None = None      # final transcript arrived
    turn_start: float | None = None         # LLM request issued
    llm_first_token: float | None = None    # first LLM sentence/token emitted
    tts_first_audio: float | None = None    # first TTS audio chunk received
    first_frame_out: float | None = None    # first reply frame sent to carrier
    emitted: bool = False

    def mark(self, stage: str, when: float | None = None) -> None:
        """Stamp ``stage`` once. Re-marking the same stage is a no-op so the
        FIRST occurrence wins (e.g. first audio frame, not the last)."""
        if getattr(self, stage) is None:
            setattr(self, stage, when if when is not None else now())

    @staticmethod
    def _ms(a: float | None, b: float | None) -> int | None:
        if a is None or b is None or b < a:
            return None
        return round((b - a) * 1000)

    def breakdown(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for start, end, label in _SEGMENTS:
            d = self._ms(getattr(self, start), getattr(self, end))
            if d is not None:
                out[label] = d
        # Headline: prefer caller-stop anchor, fall back to transcript arrival
        # so a turn started from text (no VAD) still reports perceived latency.
        anchor = self.user_speech_end if self.user_speech_end is not None else self.transcript_in
        perceived = self._ms(anchor, self.first_frame_out)
        if perceived is not None:
            out["perceived_ms"] = perceived
        return out

    def emit(self) -> dict[str, int]:
        """Log the breakdown exactly once and return it."""
        b = self.breakdown()
        if not self.emitted:
            self.emitted = True
            logger.info("turn.latency", turn_id=self.turn_id, kind=self.kind, **b)
        return b
