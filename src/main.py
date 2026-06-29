"""Voice Agent — telephony entrypoint.

FastAPI app that:
  1. Answers Telnyx calls and starts a bidirectional media stream (PCMU 8kHz).
  2. Bridges the media WebSocket to the STT/LLM/TTS pipeline.
  3. Delegates all turn-taking and barge-in to :class:`TurnEngine`.

The brain lives in ``src.pipeline.turn_engine``; this module is just I/O glue.
"""

import asyncio
import audioop
import base64
import json

import httpx
import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse

from src.config import settings
from src.audio.codec import Resampler
from src.security.telnyx import verify_telnyx_signature
from src.services.stt.sarvam import SarvamSTTClient
from src.services.llm.sarvam import SarvamLLMClient
from src.services.tts.sarvam import SarvamTTSClient
from src.pipeline.filler import FillerPlayer
from src.pipeline.turn_engine import TurnEngine

structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
logger = structlog.get_logger()

app = FastAPI(title="Voice Agent", version="0.7.0")


@app.get("/health")
async def health():
    return {"status": "ok"}


# ─── Telnyx call-control webhook ───

@app.post("/webhook/telnyx")
async def telnyx_webhook(request: Request):
    raw = await request.body()
    if not verify_telnyx_signature(
        public_key_b64=settings.telnyx_public_key,
        signature_b64=request.headers.get("telnyx-signature-ed25519", ""),
        timestamp=request.headers.get("telnyx-timestamp", ""),
        payload=raw,
    ):
        logger.warning("telnyx.webhook.invalid_signature")
        return JSONResponse({"error": "invalid signature"}, status_code=401)

    body = json.loads(raw or b"{}")
    et = body.get("data", {}).get("event_type", "")
    p = body.get("data", {}).get("payload", {})
    ccid = p.get("call_control_id", "")
    auth = {"Authorization": f"Bearer {settings.telnyx_api_key}", "Content-Type": "application/json"}

    if et == "call.initiated":
        async with httpx.AsyncClient() as c:
            await c.post(
                f"https://api.telnyx.com/v2/calls/{ccid}/actions/answer",
                headers=auth, json={},
            )
    elif et == "call.answered":
        url = settings.public_url.replace("https://", "wss://") + "/media-stream"
        async with httpx.AsyncClient() as c:
            await c.post(
                f"https://api.telnyx.com/v2/calls/{ccid}/actions/streaming_start",
                headers=auth,
                json={
                    "stream_url": url,
                    "stream_track": "inbound_track",
                    "stream_bidirectional_mode": "rtp",
                    "stream_bidirectional_codec": "PCMU",
                },
            )
    return JSONResponse({"status": "ok"})


# ─── Media stream ───

@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    await websocket.accept()

    stt = SarvamSTTClient(settings.sarvam_api_key, buffer_ms=settings.stt_buffer_ms)
    llm = SarvamLLMClient(settings.sarvam_api_key)
    tts = SarvamTTSClient(settings.sarvam_api_key, model=settings.sarvam_tts_model)
    resampler = Resampler()

    filler = None
    if settings.enable_fillers:
        filler = FillerPlayer(settings.default_language)
        filler.load()

    async def send_media(frame: bytes) -> None:
        await websocket.send_text(json.dumps({
            "event": "media",
            "media": {"payload": base64.b64encode(frame).decode("ascii")},
        }))

    try:
        # Wait for the stream-start frame.
        while True:
            if json.loads(await websocket.receive_text()).get("event") == "start":
                break
        logger.info("call.started")

        await stt.connect(language=settings.default_language)
        await tts.connect(
            language=settings.default_language,
            voice=settings.sarvam_tts_voice,
            sample_rate="8000",
        )

        engine = TurnEngine(
            stt=stt,
            llm=llm,
            tts=tts,
            send_media=send_media,
            system_prompt=settings.system_prompt,
            greeting_text=settings.greeting_text,
            filler=filler,
            enable_fillers=settings.enable_fillers,
            min_words=settings.bargein_min_words,
            false_timeout_s=settings.bargein_false_timeout_ms / 1000,
            enable_recovery=settings.bargein_enable_recovery,
        )

        async def audio_reader():
            """Telnyx PCMU 8kHz -> PCM16 16kHz -> STT."""
            while True:
                try:
                    d = json.loads(await websocket.receive_text())
                except WebSocketDisconnect:
                    return
                except Exception:
                    continue
                ev = d.get("event")
                if ev == "media":
                    b64 = d.get("media", {}).get("payload", "")
                    if b64:
                        # Inbound is mu-law (PCMU), NOT A-law. ulaw2lin is correct.
                        pcm8 = audioop.ulaw2lin(base64.b64decode(b64), 2)
                        await stt.send_audio(resampler.up_8k_to_16k(pcm8))
                elif ev == "stop":
                    return

        reader = asyncio.create_task(audio_reader())
        engine_task = asyncio.create_task(engine.run())
        done, pending = await asyncio.wait(
            {reader, engine_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    except Exception as e:
        logger.error("media_stream.error", error=str(e))
    finally:
        await stt.close()
        await tts.close()
        await llm.close()
        logger.info("call.cleanup")
