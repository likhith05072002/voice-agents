"""Voice Agent v7 — Production barge-in with guard stack.

Key fixes from v6:
- do_turn runs as background task so turn_engine stays responsive to STT events
- 20ms real-time pacing in playback pump (interruptible at frame level)
- Streaming sentence→TTS pipeline (first sentence plays while LLM generates rest)
- Greeting managed as a turn task (interruptible same as regular turns)
- Proper task cancellation + cleanup on interrupt
"""

import asyncio
import audioop
import base64
import json
import time
from enum import Enum

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

app = FastAPI(title="Voice Agent", version="0.7.0")

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

BACKCHANNEL_WORDS = frozenset({
    "hmm", "hm", "mm", "uh-huh", "uh huh", "okay", "ok", "yeah", "yes",
    "haan", "ha", "accha", "ji", "theek",
    "avunu", "sare", "anu",
    "haudu", "hoon", "sari",
    "aama", "aamam",
})

ECHO_GUARD_MS = 500


class State(Enum):
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


def is_backchannel(text: str) -> bool:
    words = text.lower().strip().split()
    if len(words) > 3:
        return False
    return all(w in BACKCHANNEL_WORDS for w in words)


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
            await c.post(
                f"https://api.telnyx.com/v2/calls/{ccid}/actions/answer",
                headers={"Authorization": f"Bearer {API['telnyx']}",
                         "Content-Type": "application/json"},
                json={},
            )
    elif et == "call.answered":
        url = API["public_url"].replace("https://", "wss://") + "/media-stream"
        async with httpx.AsyncClient() as c:
            await c.post(
                f"https://api.telnyx.com/v2/calls/{ccid}/actions/streaming_start",
                headers={"Authorization": f"Bearer {API['telnyx']}",
                         "Content-Type": "application/json"},
                json={
                    "stream_url": url,
                    "stream_track": "inbound_track",
                    "stream_bidirectional_mode": "rtp",
                    "stream_bidirectional_codec": "PCMU",
                },
            )
    return JSONResponse({"status": "ok"})


async def tts_synthesize(text: str) -> list[bytes]:
    """Synthesize text via fresh Sarvam TTS connection. Returns PCM16 8kHz chunks."""
    try:
        ws = await ws_lib.connect(
            "wss://api.sarvam.ai/text-to-speech/ws",
            additional_headers={"api-subscription-key": API["sarvam"]},
        )
        await ws.send(json.dumps({
            "type": "config",
            "data": {
                "target_language_code": settings.default_language,
                "speaker": settings.sarvam_tts_voice,
                "model": settings.sarvam_tts_model,
                "speech_sample_rate": "8000",
                "output_audio_codec": "linear16",
                "send_completion_event": True,
            },
        }))
        await ws.send(json.dumps({"type": "text", "data": {"text": text}}))
        await ws.send(json.dumps({"type": "flush"}))

        chunks, got_audio = [], False
        while True:
            try:
                raw = await asyncio.wait_for(
                    ws.recv(), timeout=0.5 if got_audio else 5.0)
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


def pcm_to_pcmu_frames(pcm_chunks: list[bytes]) -> list[bytes]:
    """Convert PCM16 chunks to 160-byte PCMU frames (20ms each)."""
    frames = []
    for pcm in pcm_chunks:
        encoded = audioop.lin2ulaw(pcm, 2)
        for i in range(0, len(encoded), 160):
            frames.append(encoded[i:i + 160])
    return frames


@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    await websocket.accept()

    stt = SarvamSTTClient(API["sarvam"])
    llm = SarvamLLMClient(API["sarvam"])
    history: list[dict] = []
    state = State.LISTENING
    interrupt = asyncio.Event()
    speaking_since = 0.0
    current_turn: asyncio.Task | None = None

    playback_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    playback_done = asyncio.Event()
    playback_done.set()

    try:
        while True:
            raw = await websocket.receive_text()
            if json.loads(raw).get("event") == "start":
                break

        await stt.connect(language=settings.default_language)
        logger.info("call.started")

        # ── Playback pump: 20ms paced, interruptible ──
        async def playback_pump():
            while True:
                chunk = await playback_queue.get()
                if chunk is None:
                    playback_done.set()
                    continue
                playback_done.clear()
                if interrupt.is_set():
                    while not playback_queue.empty():
                        try:
                            playback_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    playback_done.set()
                    continue
                try:
                    await websocket.send_text(json.dumps({
                        "event": "media",
                        "media": {
                            "payload": base64.b64encode(chunk).decode("ascii"),
                        },
                    }))
                except Exception:
                    break
                await asyncio.sleep(0.02)

        # ── Audio reader: Telnyx → STT ──
        async def audio_reader():
            while True:
                try:
                    raw = await websocket.receive_text()
                    d = json.loads(raw)
                    if d.get("event") == "media":
                        b64 = d.get("media", {}).get("payload", "")
                        if b64:
                            pcm8 = audioop.alaw2lin(base64.b64decode(b64), 2)
                            await stt.send_audio(resample_8k_to_16k(pcm8))
                    elif d.get("event") == "stop":
                        return
                except WebSocketDisconnect:
                    return
                except Exception:
                    continue

        # ── Interrupt helper ──
        def trigger_interrupt():
            nonlocal state, current_turn
            interrupt.set()
            while not playback_queue.empty():
                try:
                    playback_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            playback_done.set()
            if current_turn and not current_turn.done():
                current_turn.cancel()
            if history and history[-1]["role"] == "assistant":
                history.pop()
            state = State.LISTENING
            logger.info("INTERRUPTED")

        # ── Greeting (runs as a managed turn task) ──
        async def do_greeting():
            nonlocal state, speaking_since
            try:
                state = State.SPEAKING
                speaking_since = time.perf_counter()
                playback_done.clear()

                chunks = await tts_synthesize(
                    "నమస్కారం! నమ శ్రీనివాస జ్యూవెల్లరీ కి స్వాగతం. "
                    "మీకు ఏమి సహాయం చేయగలను?")

                for frame in pcm_to_pcmu_frames(chunks):
                    if interrupt.is_set():
                        return
                    await playback_queue.put(frame)

                await playback_queue.put(None)
                await playback_done.wait()

                if not interrupt.is_set():
                    state = State.LISTENING
                logger.info("greeting.done",
                            ms=round((time.perf_counter() - speaking_since) * 1000))
            except asyncio.CancelledError:
                pass

        # ── Process one conversation turn ──
        async def do_turn(transcript: str):
            nonlocal state, speaking_since
            interrupt.clear()
            t0 = time.perf_counter()
            state = State.THINKING

            history.append({"role": "user", "content": transcript})
            msgs = [{"role": "system", "content": SYSTEM_PROMPT}] + history[-10:]

            sq: asyncio.Queue = asyncio.Queue()
            lt = asyncio.create_task(llm.generate_sentences(msgs, sq))
            full_resp = ""
            first_audio = True

            try:
                while True:
                    if interrupt.is_set():
                        llm.cancel()
                        break
                    try:
                        e = await asyncio.wait_for(sq.get(), timeout=0.05)
                    except asyncio.TimeoutError:
                        continue
                    if e is None:
                        break
                    if not isinstance(e, SentenceEvent):
                        continue

                    sentence = e.text
                    full_resp += sentence

                    if interrupt.is_set():
                        break

                    chunks = await tts_synthesize(sentence)
                    if interrupt.is_set() or not chunks:
                        break

                    if first_audio:
                        state = State.SPEAKING
                        speaking_since = time.perf_counter()
                        playback_done.clear()
                        logger.info("first_audio",
                                    pipeline_ms=round((speaking_since - t0) * 1000))
                        first_audio = False

                    for frame in pcm_to_pcmu_frames(chunks):
                        if interrupt.is_set():
                            break
                        await playback_queue.put(frame)

                lt.cancel()
                try:
                    await lt
                except (asyncio.CancelledError, Exception):
                    pass

                if interrupt.is_set() or not full_resp.strip():
                    if not full_resp.strip():
                        state = State.LISTENING
                    return

                await playback_queue.put(None)
                await playback_done.wait()

                if not interrupt.is_set():
                    history.append({"role": "assistant", "content": full_resp})
                    total = round((time.perf_counter() - t0) * 1000)
                    logger.info("turn.done", ms=total, text=full_resp[:60])

                state = State.LISTENING

            except asyncio.CancelledError:
                llm.cancel()
                lt.cancel()
                try:
                    await lt
                except (asyncio.CancelledError, Exception):
                    pass

        # ── Turn engine: the brain ──
        async def turn_engine():
            nonlocal current_turn

            current_turn = asyncio.create_task(do_greeting())

            while True:
                evt = await stt.get_event()
                if evt is None:
                    break

                if isinstance(evt, TranscriptEvent) and evt.text.strip():
                    txt = evt.text.strip()
                    logger.info("stt.transcript", state=state.value, text=txt[:60])

                    if state == State.SPEAKING:
                        elapsed = (time.perf_counter() - speaking_since) * 1000

                        if elapsed < ECHO_GUARD_MS:
                            logger.debug("guard.echo", ms=round(elapsed))
                            continue

                        if is_backchannel(txt):
                            logger.info("guard.backchannel", text=txt)
                            continue

                        logger.info("BARGE_IN", text=txt[:40],
                                    after_ms=round(elapsed))
                        trigger_interrupt()
                        current_turn = asyncio.create_task(do_turn(txt))
                        continue

                    if state == State.THINKING:
                        logger.info("INTERRUPT_THINKING", text=txt[:40])
                        trigger_interrupt()
                        current_turn = asyncio.create_task(do_turn(txt))
                        continue

                    if current_turn and not current_turn.done():
                        continue
                    current_turn = asyncio.create_task(do_turn(txt))

        await asyncio.gather(
            playback_pump(),
            audio_reader(),
            turn_engine(),
        )

    except Exception as e:
        logger.error("error", error=str(e))
    finally:
        if current_turn and not current_turn.done():
            current_turn.cancel()
        await stt.close()
        await llm.close()
        logger.info("cleanup")


@app.on_event("startup")
async def startup():
    logger.info("starting")
