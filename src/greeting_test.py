"""
GREETING TEST — Uses the EXACT same send method as the echo test (which worked).
Generates a namaskaram greeting via Sarvam TTS at 8kHz, encodes to PCMA,
and sends using the proven payload format.

The ONLY difference from echo test: we send generated audio instead of echoed audio.
"""

import asyncio
import audioop
import base64
import json
import os

import httpx
import structlog
import websockets as ws_lib
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

load_dotenv()
structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
logger = structlog.get_logger()

app = FastAPI()

TELNYX_API_KEY = os.getenv("TELNYX_API_KEY")
PUBLIC_URL = os.getenv("PUBLIC_URL")
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")

# Pre-generate greeting audio at startup
GREETING_PCMA = None


async def generate_greeting():
    """Generate namaskaram greeting via Sarvam TTS, return as PCMA 8kHz bytes."""
    global GREETING_PCMA

    tts = await ws_lib.connect(
        "wss://api.sarvam.ai/text-to-speech/ws",
        additional_headers={"api-subscription-key": SARVAM_API_KEY},
    )
    await tts.send(json.dumps({"type": "config", "data": {
        "target_language_code": "te-IN", "speaker": "anushka",
        "model": "bulbul:v2", "speech_sample_rate": "8000",
        "output_audio_codec": "linear16",
        "send_completion_event": True,
    }}))
    await tts.send(json.dumps({"type": "text", "data": {
        "text": "నమస్కారం! నమ శ్రీనివాస జ్యూవెల్లరీ కి కాల్ చేసినందుకు ధన్యవాదాలు. మీకు ఏమి సహాయం చేయగలను?"
    }}))
    await tts.send(json.dumps({"type": "flush"}))

    chunks = []
    while True:
        try:
            raw = await asyncio.wait_for(tts.recv(), timeout=5.0)
            msg = json.loads(raw)
            if msg.get("type") == "audio":
                chunks.append(base64.b64decode(msg["data"]["audio"]))
            elif msg.get("type") in ("event", "error"):
                break
        except asyncio.TimeoutError:
            break

    await tts.close()

    if chunks:
        pcm16_8k = b"".join(chunks)
        # Convert PCM16 8kHz -> PCMU (mu-law) — this worked (Telugu heard!)
        GREETING_PCMA = audioop.lin2ulaw(pcm16_8k, 2)
        logger.info("greeting.ready", pcm16_bytes=len(pcm16_8k), pcma_bytes=len(GREETING_PCMA),
                     duration_ms=len(GREETING_PCMA) / 8)
    else:
        logger.error("greeting.failed")


@app.on_event("startup")
async def startup():
    await generate_greeting()


@app.get("/health")
async def health():
    return {"status": "greeting_test", "greeting_ready": GREETING_PCMA is not None}


@app.post("/webhook/telnyx")
async def webhook(request: Request):
    body = await request.json()
    event_type = body.get("data", {}).get("event_type", "")
    payload = body.get("data", {}).get("payload", {})
    call_control_id = payload.get("call_control_id", "")

    logger.info("webhook", event_type=event_type)

    if event_type == "call.initiated":
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telnyx.com/v2/calls/{call_control_id}/actions/answer",
                headers={"Authorization": f"Bearer {TELNYX_API_KEY}", "Content-Type": "application/json"},
                json={},
            )

    elif event_type == "call.answered":
        stream_url = PUBLIC_URL.replace("https://", "wss://") + "/media-stream"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.telnyx.com/v2/calls/{call_control_id}/actions/streaming_start",
                headers={"Authorization": f"Bearer {TELNYX_API_KEY}", "Content-Type": "application/json"},
                json={
                    "stream_url": stream_url,
                    "stream_track": "inbound_track",
                    "stream_bidirectional_mode": "rtp",
                    "stream_bidirectional_codec": "PCMU",
                },
            )
            logger.info("streaming_start", status=resp.status_code)

    return JSONResponse({"status": "ok"})


@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    await websocket.accept()
    logger.info("ws.accepted")

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            event = data.get("event", "")

            if event == "start":
                fmt = data.get("start", {}).get("media_format", {})
                codec = fmt.get("encoding", "PCMA")
                logger.info("stream.start", codec=codec)

                if GREETING_PCMA is None:
                    logger.error("no_greeting")
                    continue

                # Send greeting — send ALL chunks immediately (no pacing)
                # Telnyx buffers and plays at correct rate
                chunk_size = 160  # 20ms at 8kHz PCMU
                chunks_sent = 0
                for i in range(0, len(GREETING_PCMA), chunk_size):
                    chunk = GREETING_PCMA[i:i + chunk_size]
                    payload_b64 = base64.b64encode(chunk).decode("ascii")
                    await websocket.send_text(json.dumps({
                        "event": "media",
                        "media": {"payload": payload_b64},
                    }))
                    chunks_sent += 1

                logger.info("greeting.sent", chunks=chunks_sent,
                            total_bytes=len(GREETING_PCMA),
                            duration_ms=len(GREETING_PCMA) / 8)

            elif event == "media":
                # Just ignore inbound audio for this test
                pass

            elif event == "stop":
                break

    except WebSocketDisconnect:
        logger.info("ws.disconnected")
    except Exception as e:
        logger.error("ws.error", error=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.greeting_test:app", host="0.0.0.0", port=8000)
