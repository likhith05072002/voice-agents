"""
MINIMAL ECHO TEST — strips everything down to the bare minimum.
Receives audio from Telnyx, immediately sends it back unchanged.
If caller hears their own echo, the plumbing works.
If silence or crash, the WebSocket send format is wrong.
"""

import asyncio
import base64
import json
import os

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


@app.get("/health")
async def health():
    return {"status": "echo_test"}


@app.post("/webhook/telnyx")
async def webhook(request: Request):
    body = await request.json()
    event_type = body.get("data", {}).get("event_type", "")
    payload = body.get("data", {}).get("payload", {})
    call_control_id = payload.get("call_control_id", "")

    logger.info("webhook", event_type=event_type)

    if event_type == "call.initiated":
        # Just answer
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telnyx.com/v2/calls/{call_control_id}/actions/answer",
                headers={"Authorization": f"Bearer {TELNYX_API_KEY}", "Content-Type": "application/json"},
                json={},
            )

    elif event_type == "call.answered":
        # Start bidirectional streaming
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
            logger.info("streaming_start", status=resp.status_code, body=resp.text[:200])

    return JSONResponse({"status": "ok"})


@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    await websocket.accept()
    logger.info("ws.accepted")

    chunk_count = 0

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            event = data.get("event", "")

            if event == "start":
                start = data.get("start", {})
                fmt = start.get("media_format", {})
                logger.info("stream.start", format=fmt, call_id=str(start.get("call_control_id", ""))[:30])

            elif event == "media":
                payload = data.get("media", {}).get("payload", "")
                if not payload:
                    continue

                chunk_count += 1

                # ECHO: send the EXACT same payload back unchanged
                echo_msg = json.dumps({
                    "event": "media",
                    "media": {
                        "payload": payload,
                    },
                })
                await websocket.send_text(echo_msg)

                if chunk_count <= 3 or chunk_count % 100 == 0:
                    raw_bytes = base64.b64decode(payload)
                    logger.info("echo", chunk=chunk_count, payload_bytes=len(raw_bytes))

            elif event == "stop":
                logger.info("stream.stop")
                break

    except WebSocketDisconnect:
        logger.info("ws.disconnected")
    except Exception as e:
        logger.error("ws.error", error=str(e))

    logger.info("done", total_chunks=chunk_count)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.echo_test:app", host="0.0.0.0", port=8000)
