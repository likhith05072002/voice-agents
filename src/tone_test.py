"""
TONE TEST — Sends a simple generated tone to the caller.
No filler files, no TTS, no STT. Just a pure sine wave tone
encoded directly as PCMA and sent in 160-byte chunks.

If caller hears the tone, our audio encoding works.
"""

import asyncio
import audioop
import base64
import json
import math
import os
import struct

import httpx
import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

load_dotenv()
structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
logger = structlog.get_logger()

app = FastAPI()

TELNYX_API_KEY = os.getenv("TELNYX_API_KEY")
PUBLIC_URL = os.getenv("PUBLIC_URL")


def generate_pcma_tone(duration_ms=3000, freq=440, sample_rate=8000):
    """Generate A-law encoded tone. Returns bytes ready for Telnyx."""
    num_samples = int(sample_rate * duration_ms / 1000)
    pcm_samples = []
    for i in range(num_samples):
        t = i / sample_rate
        val = int(16000 * math.sin(2 * math.pi * freq * t))
        pcm_samples.append(struct.pack("<h", max(-32768, min(32767, val))))
    pcm16 = b"".join(pcm_samples)
    return audioop.lin2alaw(pcm16, 2)


@app.get("/health")
async def health():
    return {"status": "tone_test"}


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

                # Generate 3 seconds of tone in the DETECTED codec
                if codec == "PCMA":
                    tone_data = generate_pcma_tone(3000, 440)
                    logger.info("tone.generated", codec="PCMA", total_bytes=len(tone_data))
                else:
                    # PCMU
                    num_samples = 8000 * 3
                    pcm_samples = []
                    for i in range(num_samples):
                        t = i / 8000
                        val = int(16000 * math.sin(2 * math.pi * 440 * t))
                        pcm_samples.append(struct.pack("<h", max(-32768, min(32767, val))))
                    pcm16 = b"".join(pcm_samples)
                    tone_data = audioop.lin2ulaw(pcm16, 2)
                    logger.info("tone.generated", codec="PCMU", total_bytes=len(tone_data))

                # Send tone in 160-byte chunks with 20ms pacing
                chunk_size = 160
                chunks_sent = 0
                for i in range(0, len(tone_data), chunk_size):
                    chunk = tone_data[i:i + chunk_size]
                    payload_b64 = base64.b64encode(chunk).decode("ascii")
                    msg = json.dumps({
                        "event": "media",
                        "media": {
                            "payload": payload_b64,
                        },
                    })
                    await websocket.send_text(msg)
                    chunks_sent += 1
                    await asyncio.sleep(0.02)  # 20ms pacing

                logger.info("tone.sent", chunks=chunks_sent)

            elif event == "stop":
                break

    except WebSocketDisconnect:
        logger.info("ws.disconnected")
    except Exception as e:
        logger.error("ws.error", error=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.tone_test:app", host="0.0.0.0", port=8000)
