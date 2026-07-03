"""TesterCore — transport-agnostic heart of the AI voice tester.

Owns everything that is NOT the wire: the speech queue (what the tester says),
frame accounting for the agent's audio (what it hears), all timing measurements,
a two-sided mixed recording, and live-listen broadcasting. Transports drive it:

  - Loopback: a WebSocket client to our own /media-stream (free, instant).
  - PSTN: the server-side media stream of a real Telnyx call (two numbers).

Every 20 ms tick a transport calls ``next_out_frame()`` (speech or silence) and
feeds received agent audio into ``feed_agent()``. The core mixes both sides into
one conversation recording and fans it out to live listeners.
"""

from __future__ import annotations

import asyncio
import audioop
import os
import time
import wave

SAMPLES = 160                                 # 20ms @ 8kHz
FRAME_BYTES = SAMPLES * 2
SILENCE_PCM = b"\x00" * FRAME_BYTES


class TesterCore:
    def __init__(self) -> None:
        self._speech: asyncio.Queue[bytes] = asyncio.Queue()
        self._speech_done_t: float | None = None
        self.agent_frames = 0
        self.first_frame_t: float | None = None
        self.last_frame_t: float | None = None
        self._agent_backlog = bytearray()      # received, not yet mixed
        self._recording = bytearray()          # mixed conversation (PCM16 8k)
        self._track_tester = bytearray()       # caller-side track (analysis)
        self._track_agent = bytearray()        # agent-side track (analysis)
        self._listeners: list[asyncio.Queue[bytes]] = []

    # ─── caller side (the tester's mouth) ───

    def speak(self, pcm: bytes) -> None:
        self._speech_done_t = None
        for i in range(0, len(pcm), FRAME_BYTES):
            chunk = pcm[i:i + FRAME_BYTES]
            if len(chunk) < FRAME_BYTES:
                chunk += SILENCE_PCM[:FRAME_BYTES - len(chunk)]
            self._speech.put_nowait(chunk)

    def next_out_frame(self) -> bytes:
        """The next 20ms the tester sends: queued speech, else silence. Also
        advances the mixed recording by one tick."""
        if not self._speech.empty():
            out = self._speech.get_nowait()
            if self._speech.empty():
                self._speech_done_t = time.perf_counter()
        else:
            out = SILENCE_PCM
        agent = bytes(self._agent_backlog[:FRAME_BYTES])
        del self._agent_backlog[:len(agent)]
        if len(agent) < FRAME_BYTES:
            agent += SILENCE_PCM[:FRAME_BYTES - len(agent)]
        mixed = audioop.add(audioop.mul(out, 2, 0.6), audioop.mul(agent, 2, 0.9), 2)
        self._recording += mixed
        # Separate tracks for frame-accurate post-call analysis (L=tester, R=agent).
        self._track_tester += out
        self._track_agent += agent
        for q in list(self._listeners):
            if q.qsize() < 100:                # drop for slow listeners, never block
                q.put_nowait(mixed)
        return out

    @property
    def done_speaking_at(self) -> float | None:
        return self._speech_done_t

    # ─── agent side (the tester's ears) ───

    # A real PSTN leg streams audio CONTINUOUSLY (silence + comfort noise), so
    # frame arrival alone means nothing. Only frames with speech-level energy
    # count as "the agent is talking"; everything still feeds the recording.
    SPEECH_RMS = 300

    def feed_agent(self, pcm16: bytes) -> None:
        self._agent_backlog += pcm16
        if audioop.rms(pcm16, 2) <= self.SPEECH_RMS:
            return
        now = time.perf_counter()
        if self.first_frame_t is None:
            self.first_frame_t = now
        self.last_frame_t = now
        self.agent_frames += 1

    def agent_quiet_for(self, s: float) -> bool:
        return (self.last_frame_t is not None
                and time.perf_counter() - self.last_frame_t >= s)

    def agent_speaking(self) -> bool:
        return (self.last_frame_t is not None
                and time.perf_counter() - self.last_frame_t < 0.25)

    # ─── waits (used by the runner) ───

    async def wait_new_audio(self, frames_before: int, timeout: float) -> float | None:
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < timeout:
            if self.agent_frames > frames_before:
                return self.last_frame_t
            await asyncio.sleep(0.005)
        return None

    async def wait_quiet(self, quiet_s: float = 1.0, timeout: float = 30.0) -> bool:
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < timeout:
            if self.agent_frames and self.agent_quiet_for(quiet_s):
                return True
            await asyncio.sleep(0.02)
        return False

    async def wait_done_speaking(self, timeout: float = 30.0) -> float | None:
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < timeout:
            if self._speech.empty() and self._speech_done_t is not None:
                return self._speech_done_t
            await asyncio.sleep(0.01)
        return None

    # ─── live listeners + recording ───

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue[bytes] = asyncio.Queue()
        self._listeners.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._listeners:
            self._listeners.remove(q)

    def save_wav(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(bytes(self._recording))
        # Stereo analysis file: L = tester, R = agent — who made sound when.
        n = min(len(self._track_tester), len(self._track_agent))
        if n:
            stereo = bytearray()
            for i in range(0, n, 2):
                stereo += self._track_tester[i:i + 2] + self._track_agent[i:i + 2]
            with wave.open(path.replace(".wav", "-stereo.wav"), "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(8000)
                w.writeframes(bytes(stereo))
