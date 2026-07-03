"""Synthetic caller — I call the agent, talk to it, and measure the call.

Connects to the server's /media-stream exactly like Telnyx does (JSON frames,
base64 audio, A-law in / mu-law out), speaks questions rendered with Sarvam TTS
in a different voice, listens to the agent, and barge-ins mid-answer. Measures
what a real caller would FEEL, externally:

  - greeting: connect -> first agent audio
  - turn latency: caller stops speaking -> first agent audio
  - barge-in: caller starts talking over the agent -> agent goes silent

Run (server must be up):  python scripts/test_call.py [--port 8001]
Writes the agent side of the call to scripts/last_call_agent.wav for listening.
"""

import argparse
import asyncio
import audioop
import base64
import json
import os
import sys
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv
import websockets

load_dotenv()

FRAME_MS = 20
SAMPLES = 160                                # 20ms @ 8kHz
SILENCE_PCM = b"\x00\x00" * SAMPLES


async def tts_many_pcm8k(texts: list[str], language: str, voice: str) -> list[bytes]:
    """Render caller speech with Sarvam TTS (PCM16 8kHz) — ONE connection for
    all utterances (rapid connect/disconnect cycles get rate-limited)."""
    from src.services.tts.sarvam import SarvamTTSClient
    tts = SarvamTTSClient(os.environ["SARVAM_API_KEY"], model="bulbul:v3")
    await tts.connect(language=language, voice=voice, sample_rate="8000")
    out = []
    for text in texts:
        await tts.reset()
        await tts.send_text(text)
        await tts.flush()
        chunks = []
        while True:
            audio = await asyncio.wait_for(tts.get_audio(), timeout=20.0)
            if audio is None:
                break
            chunks.append(audio)
        out.append(b"".join(chunks))
    await tts.close()
    return out


class SyntheticCall:
    def __init__(self, url: str):
        self.url = url
        self.ws = None
        self.agent_pcm = bytearray()          # everything the agent said (PCM16 8k)
        self.last_agent_frame_t: float | None = None
        self.first_agent_frame_t: float | None = None
        self.agent_frames = 0
        self._rx_task = None

    async def connect(self):
        self.ws = await websockets.connect(self.url)
        await self.ws.send(json.dumps({
            "event": "start",
            "start": {"call_control_id": "synthetic-test-call",
                      "media_format": {"encoding": "PCMA", "sample_rate": 8000}},
        }))
        self._rx_task = asyncio.create_task(self._receive())

    async def _receive(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if msg.get("event") == "media":
                    frame = base64.b64decode(msg["media"]["payload"])
                    self.agent_pcm += audioop.ulaw2lin(frame, 2)
                    now = time.perf_counter()
                    if self.first_agent_frame_t is None:
                        self.first_agent_frame_t = now
                    self.last_agent_frame_t = now
                    self.agent_frames += 1
        except websockets.ConnectionClosed:
            pass

    def agent_quiet_for(self, s: float) -> bool:
        return (self.last_agent_frame_t is not None
                and time.perf_counter() - self.last_agent_frame_t >= s)

    async def stream_pcm(self, pcm: bytes):
        """Send caller audio as real-time-paced 20ms A-law frames."""
        alaw = audioop.lin2alaw(pcm, 2)
        for i in range(0, len(alaw), SAMPLES):
            chunk = alaw[i:i + SAMPLES]
            if len(chunk) < SAMPLES:
                chunk += audioop.lin2alaw(SILENCE_PCM, 2)[:SAMPLES - len(chunk)]
            await self.ws.send(json.dumps({
                "event": "media",
                "media": {"payload": base64.b64encode(chunk).decode()},
            }))
            await asyncio.sleep(FRAME_MS / 1000)

    async def stream_silence(self, seconds: float):
        await self.stream_pcm(SILENCE_PCM * int(seconds * 1000 / FRAME_MS))

    async def wait_agent_starts(self, timeout=15.0) -> float:
        t0 = time.perf_counter()
        base = self.agent_frames
        while time.perf_counter() - t0 < timeout:
            if self.agent_frames > base:
                return time.perf_counter()
            await asyncio.sleep(0.005)
        raise TimeoutError("agent never started speaking")

    async def wait_agent_finishes(self, quiet_s=1.0, timeout=30.0):
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < timeout:
            if self.agent_frames and self.agent_quiet_for(quiet_s):
                return
            await asyncio.sleep(0.02)
        raise TimeoutError("agent never finished speaking")

    async def close(self):
        try:
            await self.ws.send(json.dumps({"event": "stop"}))
            await self.ws.close()
        except Exception:
            pass
        if self._rx_task:
            self._rx_task.cancel()

    def save_wav(self, path: str):
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(bytes(self.agent_pcm))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--lang", default="en-IN")
    args = ap.parse_args()

    print("── synthesizing my caller voice (Sarvam TTS) ──")
    q1, q2 = await tts_many_pcm8k(
        ["Hello, what is the price of 22 carat gold today?",
         "Wait, actually tell me your shop timings instead."],
        args.lang, "abhilash")
    print(f"  q1: {len(q1)//320*20}ms   q2: {len(q2)//320*20}ms")

    call = SyntheticCall(f"ws://127.0.0.1:{args.port}/media-stream")
    await call.connect()
    t_connect = time.perf_counter()
    print("── call connected, waiting for greeting ──")

    # keep the line 'open' with silence while the greeting arrives
    silence_task = asyncio.create_task(call.stream_silence(20))
    t_greet = await call.wait_agent_starts()
    print(f"  connect -> greeting first audio : {(t_greet - t_connect)*1000:.0f} ms")
    await call.wait_agent_finishes(quiet_s=1.0)
    silence_task.cancel()
    print("  greeting finished")

    # ─── turn 1: ask, then measure caller-stops -> agent-first-audio ───
    frames_before = call.agent_frames
    await call.stream_pcm(q1)
    t_stop_talking = time.perf_counter()
    print("── asked Q1, waiting for reply ──")
    silence_task = asyncio.create_task(call.stream_silence(30))
    t0 = time.perf_counter()
    while call.agent_frames == frames_before and time.perf_counter() - t0 < 20:
        await asyncio.sleep(0.005)
    if call.agent_frames == frames_before:
        print("  !! agent never replied to Q1")
    else:
        perceived = (time.perf_counter() - t_stop_talking) * 1000
        print(f"  PERCEIVED LATENCY (caller stops -> first reply audio): {perceived:.0f} ms")

    # ─── barge-in: talk over the answer, measure time-to-silence ───
    await asyncio.sleep(0.6)                   # let the answer get going
    silence_task.cancel()
    t_barge = time.perf_counter()
    print("── barging in with Q2 mid-answer ──")
    await call.stream_pcm(q2)
    barge_frames_t = call.last_agent_frame_t
    silence_task = asyncio.create_task(call.stream_silence(30))
    # agent silent = no frame for 300ms after barge-in started
    t0 = time.perf_counter()
    silent_at = None
    while time.perf_counter() - t0 < 15:
        if call.last_agent_frame_t and call.agent_quiet_for(0.3):
            silent_at = call.last_agent_frame_t
            break
        await asyncio.sleep(0.02)
    if silent_at:
        stop_ms = max(0.0, (silent_at - t_barge)) * 1000
        print(f"  BARGE-IN: agent's last audio frame {stop_ms:.0f} ms after I started talking over it")

    # wait for the answer to Q2
    frames_before = call.agent_frames
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 20:
        if call.agent_frames > frames_before and call.agent_quiet_for(1.2):
            break
        await asyncio.sleep(0.05)
    silence_task.cancel()

    await call.close()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_call_agent.wav")
    call.save_wav(out)
    total_s = len(call.agent_pcm) / 2 / 8000
    print(f"── call done: {call.agent_frames} agent frames, {total_s:.1f}s of agent audio -> {out} ──")


if __name__ == "__main__":
    asyncio.run(main())
