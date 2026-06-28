"""Voice Agent v6 — Blueprint-compliant barge-in engine.

Architecture: Managed half-duplex with fast detect-and-yield (§2.9 of blueprint)
State machine: LISTENING → ENDPOINTING → THINKING → SPEAKING → (INTERRUPTED) → LISTENING
Barge-in: transcript-based detection + interruptible playback queue (§3, §4.7)
"""

import asyncio
import audioop
import base64
import json
import time
from enum import Enum
from collections import deque

import httpx
import structlog
import websockets as ws_lib
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse

from src.config import settings
from src.audio.codec import resample_8k_to_16k
from src.services.stt.sarvam import SarvamSTTClient, TranscriptEvent, VADEvent
from src.services.llm.sarvam import SarvamLLMClient, SentenceEvent

structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
logger = structlog.get_logger()

app = FastAPI(title="Voice Agent", version="0.6.0")

API = {
    "telnyx": settings.telnyx_api_key,
    "sarvam": settings.sarvam_api_key,
    "public_url": settings.public_url,
}

SYSTEM_PROMPT = (
    "You are Lakshmi, AI assistant at Nama Srinivasa Jewellery, Banjara Hills, Hyderabad. "
    "CRITICAL: Reply in the SAME language the customer uses. "
    "Telugu→Telugu. English→English. Hindi→Hindi. Kannada→Kannada. "
    "Keep answers SHORT: 1-2 sentences max. "
    "Shop: 10AM-9PM daily. Gold: 24K=Rs.7800/g, 22K=Rs.7150/g. "
    "Services: gold, silver, diamond jewellery, old gold exchange, hallmark."
)


class State(Enum):
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


# ─── Telnyx webhook ───

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhook/telnyx")
async def telnyx_webhook(request: Request):
    body = await request.json()
    et = body.get("data", {}).get("event_type", "")
    p = body.get("data", {}).get("payload", {})
    ccid = p.get("call_control_id", "")
    if et == "call.initiated":
        async with httpx.AsyncClient() as c:
            await c.post(f"https://api.telnyx.com/v2/calls/{ccid}/actions/answer",
                headers={"Authorization": f"Bearer {API['telnyx']}", "Content-Type": "application/json"}, json={})
    elif et == "call.answered":
        url = API["public_url"].replace("https://", "wss://") + "/media-stream"
        async with httpx.AsyncClient() as c:
            await c.post(f"https://api.telnyx.com/v2/calls/{ccid}/actions/streaming_start",
                headers={"Authorization": f"Bearer {API['telnyx']}", "Content-Type": "application/json"},
                json={"stream_url": url, "stream_track": "inbound_track",
                      "stream_bidirectional_mode": "rtp", "stream_bidirectional_codec": "PCMU"})
    return JSONResponse({"status": "ok"})


# ─── TTS helper (fresh connection, proven reliable) ───

async def tts_synthesize(text: str) -> list[bytes]:
    """Synthesize text via fresh Sarvam TTS connection. Returns list of PCM16 8kHz chunks."""
    try:
        ws = await ws_lib.connect("wss://api.sarvam.ai/text-to-speech/ws",
            additional_headers={"api-subscription-key": API["sarvam"]})
        await ws.send(json.dumps({"type": "config", "data": {
            "target_language_code": settings.default_language, "speaker": settings.sarvam_tts_voice,
            "model": settings.sarvam_tts_model, "speech_sample_rate": "8000",
            "output_audio_codec": "linear16", "send_completion_event": True}}))
        await ws.send(json.dumps({"type": "text", "data": {"text": text}}))
        await ws.send(json.dumps({"type": "flush"}))
        chunks, got_audio = [], False
        while True:
            try:
                timeout = 0.5 if got_audio else 5.0
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                msg = json.loads(raw)
                if msg.get("type") == "audio":
                    chunks.append(base64.b64decode(msg["data"]["audio"]))
                    got_audio = True
                elif msg.get("type") in ("event", "error"):
                    break
            except asyncio.TimeoutError:
                break
        await ws.close()
        return chunks
    except Exception as e:
        logger.error("tts.error", error=str(e))
        return []


# ─── The main call handler ───

@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    await websocket.accept()

    stt = SarvamSTTClient(API["sarvam"])
    llm = SarvamLLMClient(API["sarvam"])
    history = []
    state = State.LISTENING
    interrupt_flag = asyncio.Event()

    # Playback queue (blueprint §4.7): PCMU chunks ready for Telnyx
    # Each item is a 160-byte PCMU chunk (20ms of audio)
    playback_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    playback_active = False

    try:
        # Wait for stream start
        while True:
            raw = await websocket.receive_text()
            if json.loads(raw).get("event") == "start":
                break

        logger.info("call.started")
        await stt.connect(language=settings.default_language)

        # ─── TASK 1: Playback pump (drains queue → Telnyx, interruptible) ───
        async def playback_pump():
            nonlocal playback_active
            while True:
                chunk = await playback_queue.get()
                if chunk is None:
                    playback_active = False
                    continue
                playback_active = True
                if interrupt_flag.is_set():
                    # FLUSH: drain the entire queue (blueprint §3: clear playback queue)
                    while not playback_queue.empty():
                        try: playback_queue.get_nowait()
                        except: break
                    playback_active = False
                    continue
                try:
                    await websocket.send_text(json.dumps({
                        "event": "media",
                        "media": {"payload": base64.b64encode(chunk).decode("ascii")},
                    }))
                except Exception:
                    break
                # Yield control every chunk so barge-in can fire
                await asyncio.sleep(0)

        # ─── TASK 2: Audio reader (Telnyx → STT) ───
        async def audio_reader():
            while True:
                try:
                    raw = await websocket.receive_text()
                    d = json.loads(raw)
                    if d.get("event") == "media":
                        b64 = d.get("media", {}).get("payload", "")
                        if b64 and stt:
                            pcm8 = audioop.alaw2lin(base64.b64decode(b64), 2)
                            await stt.send_audio(resample_8k_to_16k(pcm8))
                    elif d.get("event") == "stop":
                        return
                except WebSocketDisconnect:
                    return
                except Exception:
                    continue

        # ─── TASK 3: STT reader + Turn Engine (the brain, blueprint §3) ───
        async def turn_engine():
            nonlocal state, interrupt_flag

            while True:
                evt = await stt.get_event()
                if evt is None:
                    break

                if isinstance(evt, TranscriptEvent) and evt.text.strip():
                    txt = evt.text.strip()

                    # ── BARGE-IN CHECK (blueprint §3.1 _barge_in_guard) ──
                    if state == State.SPEAKING:
                        # User spoke while bot is talking → INTERRUPT
                        logger.info("BARGE_IN", text=txt[:40])

                        # 1. Signal interrupt
                        interrupt_flag.set()

                        # 2. Flush playback queue (clear all pending audio)
                        while not playback_queue.empty():
                            try: playback_queue.get_nowait()
                            except: break

                        # 3. Truncate history (only keep what was played)
                        # Since we can't track exact chars played, remove last assistant turn if incomplete
                        if history and history[-1]["role"] == "assistant":
                            history.pop()

                        state = State.LISTENING
                        logger.info("INTERRUPTED", state="listening")

                        # Process the interrupting text as new turn
                        await do_turn(txt)
                        continue

                    if state == State.THINKING:
                        # Still thinking — cancel and restart
                        interrupt_flag.set()
                        await asyncio.sleep(0.1)
                        interrupt_flag = asyncio.Event()
                        await do_turn(txt)
                        continue

                    # LISTENING state: new turn
                    await do_turn(txt)

        # ─── Process one turn (LLM → TTS → queue for playback) ───
        async def do_turn(transcript: str):
            nonlocal state, interrupt_flag
            t0 = time.perf_counter()

            # Fresh interrupt flag for this turn
            interrupt_flag = asyncio.Event()
            state = State.THINKING

            history.append({"role": "user", "content": transcript})
            msgs = [{"role": "system", "content": SYSTEM_PROMPT}] + history[-10:]

            # LLM
            sq = asyncio.Queue()
            lt = asyncio.create_task(llm.generate_sentences(msgs, sq))
            resp = ""
            while True:
                if interrupt_flag.is_set():
                    llm.cancel()
                    try: await lt
                    except: pass
                    state = State.LISTENING
                    return
                try:
                    e = await asyncio.wait_for(sq.get(), timeout=0.05)
                except asyncio.TimeoutError:
                    continue
                if e is None: break
                if isinstance(e, SentenceEvent): resp += e.text
            try: await lt
            except: pass

            if not resp.strip() or interrupt_flag.is_set():
                state = State.LISTENING
                return

            lms = round((time.perf_counter() - t0) * 1000)
            logger.info("llm", ms=lms, text=resp[:60])

            # TTS
            t1 = time.perf_counter()
            chunks = await tts_synthesize(resp)
            tms = round((time.perf_counter() - t1) * 1000)

            if interrupt_flag.is_set() or not chunks:
                state = State.LISTENING
                return

            # Encode all chunks to PCMU and queue for playback
            state = State.SPEAKING
            logger.info("speaking", tts_ms=tms, chunks=len(chunks))

            for pcm_chunk in chunks:
                if interrupt_flag.is_set():
                    break
                # Convert PCM16 → PCMU, split into 160-byte (20ms) frames
                encoded = audioop.lin2ulaw(pcm_chunk, 2)
                for i in range(0, len(encoded), 160):
                    if interrupt_flag.is_set():
                        break
                    frame = encoded[i:i + 160]
                    await playback_queue.put(frame)

            # End-of-utterance marker
            await playback_queue.put(None)

            # Wait for playback to finish (or be interrupted)
            while playback_active and not interrupt_flag.is_set():
                await asyncio.sleep(0.05)

            if not interrupt_flag.is_set():
                history.append({"role": "assistant", "content": resp})
                total = round((time.perf_counter() - t0) * 1000)
                logger.info("turn.done", ms=total)

            state = State.LISTENING

        # ─── Greeting ───
        async def greeting():
            nonlocal state
            state = State.SPEAKING
            t0 = time.perf_counter()
            chunks = await tts_synthesize(
                "నమస్కారం! నమ శ్రీనివాస జ్యూవెల్లరీ కి స్వాగతం. మీకు ఏమి సహాయం చేయగలను?")
            for pcm in chunks:
                encoded = audioop.lin2ulaw(pcm, 2)
                for i in range(0, len(encoded), 160):
                    await playback_queue.put(encoded[i:i + 160])
            await playback_queue.put(None)
            while playback_active:
                await asyncio.sleep(0.05)
            state = State.LISTENING
            logger.info("greeting.done", ms=round((time.perf_counter() - t0) * 1000))

        # Run all tasks
        await asyncio.gather(
            playback_pump(),
            audio_reader(),
            turn_engine(),
            greeting(),
        )

    except Exception as e:
        logger.error("error", error=str(e))
    finally:
        await stt.close()
        await llm.close()
        logger.info("cleanup")


@app.on_event("startup")
async def startup():
    logger.info("starting")
