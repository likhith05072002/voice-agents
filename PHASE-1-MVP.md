# Phase 1: Core Voice Pipeline — Single Tenant MVP

## Goal
Get a single phone call working end-to-end: a patient calls, the AI receptionist answers in Telugu, has a natural conversation, and hangs up. No business tools, no multi-tenant — just pure voice quality and latency.

**Timeline**: 3-4 weeks
**Deliverable**: A working voice agent on Raspberry Pi 5 that handles one call at a time in Telugu with human-like conversational feel

---

## Step 0: Benchmark Sarvam-30B LLM (Day 1-2)

Before writing any pipeline code, we need real numbers for Sarvam-30B.

### What to Benchmark
```python
# scripts/benchmark_llm.py
# 
# Test matrix:
# ├── Languages: Telugu, Kannada, Hindi, Tamil, English
# ├── Context lengths: 300 tokens, 1K tokens, 3K tokens
# ├── Prompt types:
# │   ├── Greeting response ("నమస్కారం, డాక్టర్ క్లినిక్‌కి కాల్ చేసినందుకు ధన్యవాదాలు")
# │   ├── Appointment query ("రేపు మధ్యాహ్నం 3 గంటలకు అపాయింట్‌మెంట్ ఉందా?")
# │   ├── Information request ("డాక్టర్ గారి ఫీజు ఎంత?")
# │   └── Multi-turn (5 turns of context + new query)
# └── Caching: with and without prompt caching
#
# Measure:
# ├── TTFT (time to first token)
# ├── ITL (inter-token latency)
# ├── Total generation time
# ├── Response quality (manual review)
# └── Token count (input/output)
```

### Decision Gate
| TTFT Result | Strategy |
|-------------|----------|
| < 500ms | Excellent -- Sarvam-30B for everything, minimal fillers |
| 500-800ms | Good -- Sarvam-30B for all, standard fillers (300ms) |
| 800-1500ms | Acceptable -- Sarvam-30B for all, extended fillers (500-800ms) |
| > 1500ms | Hybrid -- Sarvam-30B for Indian languages with long fillers, Groq for English |

### BENCHMARK RESULTS (2026-06-27)
**CRITICAL FIX: Set `reasoning_effort=None` to disable chain-of-thought reasoning.**
Without this, TTFT is 8+ seconds (model burns 1000+ tokens on reasoning before producing content).

| Language | TTFT | Total | Response Quality |
|----------|------|-------|-----------------|
| Telugu | 388ms | 461ms | Good |
| Telugu (pain) | 360ms | 460ms | Good |
| Telugu (pricing) | 333ms | 383ms | Good |
| Hindi | 329ms | 428ms | Good |
| Kannada | 322ms | 408ms | Good |
| Tamil | 342ms | 439ms | Good |
| English | 297ms | 357ms | Good |
| Telugu (5-turn) | 347ms | 437ms | Good |
| **Average** | **340ms** | **422ms** | |

**VERDICT: EXCELLENT (340ms avg TTFT) -- Sarvam-30B is fully viable for all languages.**

### Prompt Caching Strategy
Sarvam-30B supports prompt caching (₹1.50/M cached input vs ₹2.50/M uncached).
The system prompt + clinic context (~300 tokens) stays identical across all turns in a call.
With caching, TTFT should drop significantly on turns 2+.

```
Turn 1: Full system prompt + user query → TTFT: ~1500ms (cold)
Turn 2: [CACHED system prompt] + history + user query → TTFT: ~500ms (warm)
Turn 3+: [CACHED system+history] + user query → TTFT: ~300-500ms (warm)
```

---

## Step 1: Project Scaffolding (Day 3)

### Initialize Project
```
voice-agent/
├── pyproject.toml
├── .env.example
├── src/
│   ├── __init__.py
│   ├── main.py              # FastAPI app
│   └── config.py            # pydantic-settings
└── scripts/
    └── benchmark_llm.py
```

### Dependencies (pyproject.toml)
```toml
[project]
name = "voice-agent"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "websockets>=13.0",
    "httpx>=0.27",
    "openai>=1.50",           # Sarvam LLM uses OpenAI-compatible API
    "pydantic-settings>=2.0",
    "numpy>=1.26",
    "scipy>=1.14",            # Audio resampling
    "structlog>=24.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
]
```

### Environment Variables (.env)
```bash
# Sarvam AI
SARVAM_API_KEY=your_sarvam_api_key

# Sarvam STT
SARVAM_STT_WS_URL=wss://api.sarvam.ai/speech-to-text/ws
SARVAM_STT_MODEL=saaras:v3

# Sarvam TTS
SARVAM_TTS_WS_URL=wss://api.sarvam.ai/text-to-speech/stream
SARVAM_TTS_MODEL=bulbul:v2
SARVAM_TTS_VOICE=meera        # Telugu female voice (check Sarvam docs for voice names)

# Sarvam LLM (verify exact model name in your Sarvam dashboard)
SARVAM_LLM_BASE_URL=https://api.sarvam.ai/v1
SARVAM_LLM_MODEL=sarvam-m4

# EnableX (India telephony)
ENABLEX_APP_ID=your_enablex_app_id
ENABLEX_APP_KEY=your_enablex_app_key

# Telnyx (International telephony)
TELNYX_API_KEY=your_telnyx_api_key
TELNYX_CONNECTION_ID=your_connection_id

# OpenRouter (LLM fallback)
OPENROUTER_API_KEY=your_openrouter_key

# OpenAI (LLM fallback)
OPENAI_API_KEY=your_openai_key

# Server
HOST=0.0.0.0
PORT=8000
LOG_LEVEL=debug
DEFAULT_LANGUAGE=te-IN
```

---

## Step 2: Telephony WebSocket Endpoint (Day 4-5)

### Dual Telephony: EnableX (India) + Telnyx (International)

Both providers send the **same audio format** (μ-law, 8kHz, base64 over WebSocket), so the pipeline code is shared — only the webhook/connection handling differs.

```
India calls:     Patient ──► EnableX ──► WebSocket ──► Our Server
International:   Patient ──► Telnyx  ──► WebSocket ──► Our Server

Both send identical format:
  μ-law (PCMU), 8kHz, mono, base64-encoded chunks over WebSocket
```

### System Design: WebSocket Protocol

```
EnableX Voice Streaming:
                                    
  Events we receive:
  ├── "stream.started"      → Stream started, get call metadata
  ├── "media"               → Audio chunk (base64 encoded μ-law 8kHz)
  ├── "dtmf"                → Keypad press
  └── "stream.stopped"      → Call ended
  
  Events we send:
  ├── "media"               → Audio chunk to play to caller
  └── "clear"               → Clear audio buffer (for barge-in)

  Media Stream API: POST https://api.enablex.io/voice/v1/call/:callId/stream
  WebSocket URL: wss://your-server:8000/ws/call/{call_id}

Telnyx Call Flow:
  Events we receive:
  ├── "connection.created"  → Call started
  ├── "media"               → Audio chunk (base64 encoded)
  └── "call.hangup"         → Call ended
```

### Audio Format Details (Same for Both Providers)
```
EnableX/Telnyx send: μ-law (PCMU), 8kHz, mono, base64 encoded in JSON

Sarvam STT needs:   PCM_S16LE, 16kHz, mono
Sarvam TTS sends:   PCM, 8kHz, mono (set target_sample_rate=8000)

Required conversions:
  EnableX/Telnyx (8kHz μ-law) → ulaw_to_pcm16 → resample 8k→16k → Sarvam STT
  Sarvam TTS (8kHz PCM) → pcm16_to_ulaw → base64 → EnableX/Telnyx
```

### Implementation: `src/api/websocket.py`
```python
# Unified WebSocket endpoint — works for both EnableX and Telnyx
#
# Endpoint: WebSocket /ws/call/{call_id}
# The telephony provider connects here and streams audio.
#
# On stream start (EnableX: "stream.started", Telnyx: "connection.created"):
#   1. Extract caller number, call_id, provider type
#   2. Create VoiceSession
#   3. Open Sarvam STT WebSocket
#   4. Open Sarvam TTS WebSocket
#   5. Start audio pipeline
#
# On "media" (identical for both):
#   1. Decode base64 audio
#   2. Convert μ-law → PCM16
#   3. Resample 8kHz → 16kHz
#   4. Forward to STT ingress queue
#
# On stream end:
#   1. Close STT/TTS WebSockets
#   2. Cleanup session
#   3. Log call summary
```

### EnableX: Start Media Stream
```python
# When EnableX webhook fires on incoming call:
# 1. Answer the call
# 2. Start media streaming to our WebSocket
#
# POST https://api.enablex.io/voice/v1/call/{call_id}/stream
# {
#     "url": "wss://our-server:8000/ws/call/{call_id}",
#     "track": "both"
# }
```

### Telnyx: Start Media Stream
```python
# When Telnyx webhook fires on incoming call:
# 1. Answer the call
# 2. Start media streaming to our WebSocket
#
# telnyx.Call.answer(call_control_id=cid)
# telnyx.Call.streaming_start(
#     call_control_id=cid,
#     stream_url=f"wss://our-server/ws/call/{cid}",
#     stream_track="both_tracks"
# )
```

### Telephony Abstraction: `src/services/telephony/`
```python
# base.py — Abstract interface
# class TelephonyProvider:
#     async def answer_call(self, call_id: str) -> None
#     async def start_stream(self, call_id: str, ws_url: str) -> None
#     async def send_audio(self, call_id: str, audio_b64: str) -> None
#     async def hangup(self, call_id: str) -> None
#     def parse_event(self, raw: dict) -> TelephonyEvent
#
# enablex.py — EnableX implementation
# telnyx.py  — Telnyx implementation
#
# Router picks provider based on phone number:
#   +91-XXXXXXXXXX → EnableX (India)
#   +1-XXXXXXXXXX  → Telnyx (USA/International)
```

---

## Step 3: Sarvam STT Streaming Client (Day 6-8)

### System Design: STT WebSocket Protocol

```
Connection to: wss://api.sarvam.ai/speech-to-text/ws

Step 1 — Send config message:
{
    "api_subscription_key": "...",
    "model": "saaras:v3",
    "language_code": "te-IN",
    "audio_format": "pcm_s16le",
    "sampling_rate": 16000,
    "mode": "transcribe"
}

Step 2 — Stream audio:
  Send binary frames (PCM_S16LE chunks, 16kHz)
  Continuously, as audio arrives from Telnyx

Step 3 — Receive transcripts:
{
    "type": "transcript",
    "text": "రేపు అపాయింట్‌మెంట్",
    "is_final": false,          // partial
    "language_code": "te-IN"
}
{
    "type": "transcript",
    "text": "రేపు అపాయింట్‌మెంట్ కావాలి",
    "is_final": true,           // final — turn may be complete
    "language_code": "te-IN"
}

Step 4 — End of audio:
  Send text message: "eof"
  Receive remaining transcripts
```

### Implementation: `src/services/stt/sarvam.py`
```python
# class SarvamSTTClient:
#     
#     async def connect(self, language: str) -> None:
#         """Open WebSocket, send config message"""
#     
#     async def send_audio(self, pcm_chunk: bytes) -> None:
#         """Send audio chunk to STT"""
#     
#     async def receive_loop(self) -> AsyncIterator[TranscriptEvent]:
#         """Yield partial and final transcript events"""
#         # Each event has: text, is_final, language_code, timestamp
#     
#     async def close(self) -> None:
#         """Send eof, close connection"""
#
# Key design decisions:
# - Keep connection open for entire call (no reconnect per utterance)
# - Buffer audio if WebSocket backpressures (unlikely but handle it)
# - Emit events via asyncio.Queue for the orchestrator to consume
# - Handle reconnection on unexpected disconnect
```

---

## Step 4: Sarvam LLM Streaming Client (Day 9-11)

### System Design: LLM with Sentence Detection

```
LLM Flow (per turn):

  Final transcript from STT
       │
       ▼
  Build messages array:
  [
    {"role": "system", "content": clinic_system_prompt},
    {"role": "user", "content": "prev user msg"},
    {"role": "assistant", "content": "prev assistant msg"},
    ...
    {"role": "user", "content": current_transcript}
  ]
       │
       ▼
  POST https://api.sarvam.ai/v1/chat/completions
    model: "sarvam-m4"
    stream: true
       │
       ▼
  Receive token stream:
    "నమ" → "స్కా" → "రం" → "," → " మీ" → "కు" → " ఏ" → "మి" → " సహాయం" → " చేయ" → "గల" → "ను" → "?"
       │
       ▼
  Sentence detector buffers tokens:
    Buffer: "నమస్కారం, మీకు ఏమి సహాయం చేయగలను?"
    Detected sentence boundary (?) → EMIT SENTENCE
       │
       ▼
  Sentence sent to TTS immediately
  LLM continues generating next sentence (if any)
```

### Sentence Boundary Detection
```python
# Telugu sentence endings: . ? ! । (danda)
# Also detect: \n, comma-pause (if buffer > 50 chars and comma)
#
# IMPORTANT: Don't split too aggressively
#   Bad:  "నమస్కారం," → TTS (too short, sounds choppy)
#   Good: "నమస్కారం, మీకు ఏమి సహాయం చేయగలను?" → TTS
#
# Rule: Emit on sentence-ending punctuation (. ? ! ।)
#       OR when buffer exceeds 80 chars and hits a comma/space
#       Minimum buffer: 20 characters before emitting
```

### CRITICAL LLM Parameter
```python
# ALWAYS set reasoning_effort=None for voice agent calls
# Without this, Sarvam-30B is a reasoning model that spends 8+ seconds thinking
payload = {
    "model": "sarvam-30b",
    "messages": messages,
    "stream": True,
    "max_tokens": 256,
    "reasoning_effort": None,   # <-- THIS IS THE KEY. Disables chain-of-thought.
}
```

### System Prompt (Telugu Dental Receptionist Example)
```
మీరు డాక్టర్ రెడ్డి డెంటల్ క్లినిక్ యొక్క AI రిసెప్షనిస్ట్. 
మీ పేరు "లక్ష్మి".

నియమాలు:
- ఎల్లప్పుడూ తెలుగులో మాట్లాడండి
- మర్యాదగా, స్నేహపూర్వకంగా ఉండండి
- "గారు" గౌరవ ప్రత్యయాన్ని ఉపయోగించండి
- సమాధానాలు చిన్నగా ఉంచండి (2-3 వాక్యాలు)
- మీకు తెలియకపోతే "డాక్టర్ గారిని అడిగి చెప్తాను" అని చెప్పండి

క్లినిక్ సమాచారం:
- సమయాలు: ఉదయం 9 - సాయంత్రం 6, సోమ-శని
- డాక్టర్: డా. సుధీర్ రెడ్డి
- సేవలు: దంత పరీక్ష, ఫిల్లింగ్, రూట్ కెనాల్, బ్రేసెస్
- చిరునామా: బంజారా హిల్స్, హైదరాబాద్
```

### Implementation: `src/services/llm/sarvam.py`
```python
# class SarvamLLMClient:
#     
#     async def generate_stream(
#         self, 
#         messages: list[dict],
#         on_sentence: Callable[[str], Awaitable[None]]
#     ) -> str:
#         """
#         Stream tokens from Sarvam-30B.
#         Calls on_sentence callback for each detected sentence.
#         Returns full response text.
#         """
#         # Uses httpx streaming with OpenAI-compatible API
#         # Sentence detector buffers tokens
#         # Calls on_sentence immediately when boundary detected
#         # First sentence → TTS while LLM still generating
#
#     async def cancel(self) -> None:
#         """Cancel in-flight generation (for barge-in)"""
```

---

## Step 5: Sarvam TTS Streaming Client (Day 12-13)

### System Design: TTS WebSocket Protocol

```
Connection to: wss://api.sarvam.ai/text-to-speech/stream

Step 1 — Send config:
{
    "api_subscription_key": "...",
    "model": "bulbul:v2",
    "voice": "meera",
    "target_sample_rate": 8000,       // Match Telnyx output format
    "text_aggregation_mode": "TOKEN", // Lowest latency
    "min_buffer_size": 10,            // Start synthesis after 10 chars
    "pace": 1.0,
    "temperature": 0.6
}

Step 2 — Send text:
  Send text frame: "నమస్కారం, మీకు ఏమి సహాయం చేయగలను?"
  Can send multiple text frames (sentence by sentence from LLM)

Step 3 — Receive audio chunks:
  Binary frames: PCM audio at target_sample_rate
  Arrive as synthesis progresses (streaming)
  Each chunk: ~20-100ms of audio

Step 4 — End:
  Send: "eos" (end of synthesis)
  Receive remaining audio
```

### Implementation: `src/services/tts/sarvam.py`
```python
# class SarvamTTSClient:
#
#     async def connect(self, voice: str, sample_rate: int) -> None:
#         """Open WebSocket, send config"""
#
#     async def send_text(self, text: str) -> None:
#         """Send text for synthesis (call per sentence from LLM)"""
#
#     async def receive_audio_loop(self) -> AsyncIterator[bytes]:
#         """Yield PCM audio chunks as they're synthesized"""
#
#     async def close(self) -> None:
#         """Send eos, close connection"""
#
# Key: Set target_sample_rate=8000 so Sarvam does the downsampling
#      (avoids CPU-intensive resampling on RPi 5)
```

**Optimization**: Set `target_sample_rate=8000` in TTS config so Sarvam returns audio already at Telnyx's expected rate. This eliminates the 24kHz→8kHz resampling step on our RPi 5, saving CPU.

---

## Step 6: Pipeline Orchestrator (Day 14-17)

### System Design: The Core Event Loop

This is the most critical component. It wires everything together.

```
┌──────────────────────────────────────────────────────────────┐
│                    SESSION ORCHESTRATOR                       │
│                                                              │
│  State Machine:                                              │
│                                                              │
│  IDLE ──(call starts)──► LISTENING                           │
│    │                        │                                │
│    │                    (STT partial transcripts)             │
│    │                        │                                │
│    │                    (turn detected)                       │
│    │                        │                                │
│    │                        ▼                                │
│    │                   PROCESSING                            │
│    │                     │    │                               │
│    │          (filler)───┘    └───(LLM streaming)             │
│    │             │                    │                       │
│    │             ▼                    ▼                       │
│    │          SPEAKING ◄──── (TTS audio arrives)              │
│    │             │                                           │
│    │         (speech done)                                    │
│    │             │                                           │
│    │             ▼                                           │
│    │         LISTENING ◄──── (back to listening)              │
│    │                                                         │
│    │  At any point during SPEAKING:                           │
│    │    (barge-in detected) ──► cancel TTS ──► LISTENING     │
│    │                                                         │
│    └──(call ends)──► CLEANUP ──► IDLE                        │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### Implementation: `src/pipeline/orchestrator.py`
```python
# class VoiceSessionOrchestrator:
#     """
#     Manages one active call. Created per call, destroyed on hangup.
#     
#     Runs 4 concurrent async tasks:
#       1. audio_ingress_task  — Telnyx → resample → STT
#       2. stt_consumer_task   — STT events → turn detection → trigger LLM
#       3. llm_consumer_task   — LLM sentences → TTS
#       4. audio_egress_task   — TTS audio + fillers → Telnyx
#     
#     Shared state (thread-safe via asyncio):
#       - state: SessionState enum
#       - audio_out_queue: asyncio.Queue[bytes]  (audio to send to caller)
#       - current_transcript: str (accumulates STT partials)
#       - conversation_history: list[dict] (for LLM context)
#     """
#
#     async def run(self, telnyx_ws: WebSocket):
#         """Main entry point — called when call starts"""
#         # 1. Open STT + TTS connections
#         # 2. Play greeting audio
#         # 3. Start all 4 tasks with asyncio.gather
#         # 4. Wait for call to end
#         # 5. Cleanup
#
#     async def _audio_ingress_task(self):
#         """Read audio from Telnyx, resample, forward to STT"""
#         # Runs continuously while call is active
#         # Converts μ-law 8kHz → PCM16 16kHz
#
#     async def _stt_consumer_task(self):
#         """Process STT transcripts, detect turns, trigger LLM"""
#         # On partial: update current_transcript
#         # On final: run turn detection
#         # On turn complete: 
#         #   1. Set state = PROCESSING
#         #   2. Queue filler audio
#         #   3. Start LLM generation
#
#     async def _llm_consumer_task(self):
#         """Feed LLM sentences to TTS"""
#         # Wait for sentence from LLM
#         # Send to TTS
#         # TTS audio arrives via audio_egress
#
#     async def _audio_egress_task(self):
#         """Send audio to caller with proper pacing"""
#         # Dequeue audio chunks
#         # Pace at 20ms intervals (real-time)
#         # Handle crossfade between filler and TTS
#         # Handle barge-in (flush queue)
```

### Concurrency Model (asyncio on RPi 5)
```
Single Python process, single event loop, 4 async tasks per call.

For 10 concurrent calls = 40 async tasks.
asyncio handles this easily — all I/O bound, no CPU blocking.

Only CPU-intensive work: audio resampling
Solution: Use scipy.signal.resample in a thread pool executor
  loop.run_in_executor(None, resample, audio_chunk)
  
RPi 5 has 4 cores — ThreadPoolExecutor(max_workers=4) for resampling
```

---

## Step 7: Filler Audio System (Day 18-19)

### System Design: Filler Architecture

```
Filler Audio Bank (pre-generated at startup):

assets/fillers/telugu/
├── acknowledge/         # Response to statements
│   ├── avunu_1.pcm     # "అవును..." (300ms)
│   ├── avunu_2.pcm     # "అవును..." (250ms, different intonation)
│   └── aunu_1.pcm      # "ఔను..." (200ms)
├── thinking/            # When processing
│   ├── hmm_1.pcm       # "హ్మ్..." (400ms)
│   ├── hmm_2.pcm       # "హ్మ్మ్..." (350ms)
│   └── chustanu_1.pcm  # "చూస్తాను..." (500ms)
├── understanding/       # After hearing a request
│   ├── sare_1.pcm      # "సరే..." (300ms)
│   ├── okay_1.pcm      # "ఓకే..." (250ms)
│   └── theekhe_1.pcm   # "తీకే..." (300ms)
└── greeting/            # Opening
    └── namaskaram_1.pcm # "నమస్కారం..." (600ms)
```

### Filler Selection Logic
```python
# class FillerSelector:
#     """
#     Context-aware filler selection.
#     
#     Rules:
#     1. After a question → "thinking" filler ("hmm", "chustanu")
#     2. After a statement → "acknowledge" filler ("avunu", "sare")
#     3. After greeting → "greeting" filler
#     4. Never repeat same filler twice in a row
#     5. Vary intonation (use different variants)
#     """
#
#     def select(self, transcript: str, language: str) -> bytes:
#         # Detect if question (ends with ?, ఏ, ఎ, ఎంత, ఎప్పుడు)
#         # Detect if greeting (నమస్కారం, హలో)
#         # Pick appropriate category
#         # Random variant within category (avoid last used)
#         # Return PCM audio bytes
```

### Filler Generation Script
```python
# scripts/generate_fillers.py
#
# Uses Sarvam TTS to generate filler audio for each language.
# Run once at setup, cache the results.
#
# For each language (te, kn, hi, ta, en):
#   For each filler text:
#     Call Sarvam TTS REST API (not streaming — small text)
#     Save as PCM file (8kHz, 16-bit, mono — ready for Telnyx)
#
# Cost: ~50 fillers × 5 languages × ~5 chars each = ~1,250 chars
#       = ₹1.88 (negligible, one-time cost)
```

---

## Step 8: Turn Detection (Day 20-21)

### System Design: Hybrid Turn Detector

```
Audio Stream ──► VAD ──► Silence Timer
                           │
STT Stream ──► Final Flag ─┤
                           │
                     ┌─────▼─────┐
                     │   DECISION │
                     │            │
                     │ STT final  │
                     │     +      │──► TURN COMPLETE
                     │ Silence    │
                     │  > 250ms   │
                     └────────────┘

Edge cases:
├── User pauses mid-sentence (thinking)
│   → STT doesn't send final → wait up to 800ms → hard cutoff
│
├── User says "um" or "uh"
│   → VAD stays active → no silence → no false trigger
│
├── Background noise (traffic, TV)
│   → VAD energy threshold filters low-energy noise
│
└── User asks rapid follow-up
    → Short silence (150ms) + STT final = immediate turn
```

### Implementation: `src/pipeline/turn_detector.py`
```python
# class TurnDetector:
#     SILENCE_THRESHOLD_MS = 250    # After STT final
#     HARD_CUTOFF_MS = 800          # Max wait without STT final
#     
#     async def process_stt_event(self, event: TranscriptEvent) -> bool:
#         """Returns True if turn is complete"""
#     
#     async def process_vad_event(self, is_speech: bool) -> bool:
#         """Returns True if turn is complete"""
#     
#     # VAD: Use Silero VAD (lightweight, runs on CPU)
#     # Processes 30ms audio frames
#     # Returns speech probability (0.0 - 1.0)
#     # Threshold: 0.5 for speech detection
```

---

## Step 9: Barge-In Handling (Day 22-23)

### System Design: Interruption Controller

```
During SPEAKING state:

  Audio from Telnyx ──► VAD ──► Speech detected?
                                    │
                              YES   │   NO
                                ▼       └── continue playing
                           Duration > 200ms?
                              (avoid false triggers from noise)
                                │
                          YES   │   NO
                            ▼       └── ignore (probably backchannel)
                       BARGE IN:
                       1. Cancel LLM generation (if still running)
                       2. Close TTS WebSocket
                       3. Flush audio_out_queue
                       4. Send "clear" to Telnyx (stop playback)
                       5. Set state = LISTENING
                       6. Process new speech through STT

  Target: < 150ms from detection to silence
```

### Implementation: `src/pipeline/barge_in.py`
```python
# class BargeInController:
#     MIN_SPEECH_DURATION_MS = 200   # Ignore very short sounds
#     
#     async def check(self, vad_result: float, state: SessionState) -> bool:
#         """Returns True if barge-in should be triggered"""
#         # Only active during SPEAKING state
#         # Tracks speech duration
#         # Returns True when sustained speech detected
#     
#     async def execute(self, session: VoiceSession) -> None:
#         """Execute barge-in: cancel everything, resume listening"""
#         # 1. session.llm_client.cancel()
#         # 2. session.tts_client.close()
#         # 3. session.audio_out_queue.clear()
#         # 4. send_clear_to_telnyx(session.telnyx_ws)
#         # 5. session.state = LISTENING
#         # 6. Reopen TTS connection for next response
```

---

## Step 10: Audio Processing (Day 24-25)

### System Design: Audio Pipeline

```
INGRESS (Telnyx → STT):
  Base64 decode → μ-law to PCM16 → resample 8kHz→16kHz → STT

EGRESS (TTS/Filler → Telnyx):
  PCM audio → ensure 8kHz → μ-law encode → Base64 → JSON → Telnyx

Chunk sizes:
  Telnyx sends/expects: 20ms chunks (160 samples at 8kHz = 320 bytes PCM16)
  STT expects: continuous stream (any chunk size)
  TTS returns: variable chunks (depends on synthesis)
```

### Implementation: `src/audio/`
```python
# resampler.py:
#   resample_8k_to_16k(pcm: bytes) -> bytes
#   resample_16k_to_8k(pcm: bytes) -> bytes  (if needed)
#   resample_24k_to_8k(pcm: bytes) -> bytes  (if TTS doesn't support 8k output)
#   Using scipy.signal.resample_poly for efficiency on RPi 5

# codec.py:
#   ulaw_to_pcm16(ulaw: bytes) -> bytes     # μ-law decoding
#   pcm16_to_ulaw(pcm: bytes) -> bytes      # μ-law encoding
#   base64_decode(b64: str) -> bytes
#   base64_encode(raw: bytes) -> str

# vad.py:
#   class SileroVAD:
#       def process_frame(self, pcm_16k: bytes) -> float:
#           """Returns speech probability 0.0-1.0"""
#       # Uses torch.jit model from silero-vad
#       # Processes 30ms frames (480 samples at 16kHz)
#       # Lightweight enough for RPi 5

# mixer.py:
#   crossfade(audio_a: bytes, audio_b: bytes, duration_ms: int) -> bytes
#       """Crossfade between filler and TTS audio"""
#       # Linear crossfade over duration_ms
#       # Prevents abrupt audio transitions
```

### RPi 5 Optimization Notes
```
Audio resampling is the main CPU cost on RPi 5.

Optimization 1: Set Sarvam TTS target_sample_rate=8000
  → Eliminates 24kHz→8kHz resampling entirely
  → Sarvam does it server-side (free)

Optimization 2: Use audioop module for μ-law conversion
  → C-implemented, fast on ARM
  → import audioop; audioop.ulaw2lin(data, 2)

Optimization 3: scipy.signal.resample_poly for 8k→16k
  → More efficient than resample() for integer ratios
  → resample_poly(audio, up=2, down=1) for 8k→16k

After optimizations:
  CPU per call: ~0.1 core (down from 0.3)
  RPi 5 capacity: ~20-30 concurrent calls (up from 10-15)
```

---

## Step 11: Integration Test — Full Call (Day 26-28)

### Test Plan

1. **Echo test**: Telnyx → receive audio → play back same audio → verify round trip works
2. **STT test**: Speak Telugu → see transcript in logs → verify accuracy
3. **LLM test**: Send transcript → get LLM response → verify quality
4. **TTS test**: Send text → receive audio → play to caller → verify voice quality
5. **Full pipeline**: Speak → STT → LLM → TTS → hear response
6. **Filler test**: Speak → hear filler immediately → then hear response
7. **Barge-in test**: While bot speaks → interrupt → bot stops → listens
8. **Multi-turn**: Have a 5-turn conversation → verify context is maintained
9. **Latency measurement**: Log timestamps at each stage → calculate e2e latency

### Latency Logging
```python
# Add timing to every stage:
# 
# log.info("pipeline.timing", 
#     stt_final_ms=stt_end - stt_start,
#     turn_detect_ms=turn_end - stt_end,
#     filler_start_ms=filler_start - turn_end,  # Should be ~0ms
#     llm_ttft_ms=first_token - turn_end,
#     llm_sentence_ms=sentence_end - turn_end,
#     tts_ttfa_ms=first_audio - sentence_end,
#     e2e_measured_ms=first_audio_to_caller - stt_end,
#     e2e_perceived_ms=filler_start - stt_end,  # Should be ~0ms
# )
```

---

## Phase 1 Deliverables Checklist

- [ ] Sarvam-30B benchmark results document
- [ ] Working Telnyx WebSocket integration (receive/send audio)
- [ ] Sarvam STT streaming client (live transcription)
- [ ] Sarvam LLM streaming client with sentence detection
- [ ] Sarvam TTS streaming client
- [ ] Pipeline orchestrator wiring all components
- [ ] Filler audio system (Telugu)
- [ ] Turn detection (hybrid VAD + STT)
- [ ] Barge-in handling
- [ ] Audio resampling/codec layer
- [ ] End-to-end voice conversation working
- [ ] Latency measurements logged

## Phase 1 Does NOT Include
- Multi-tenant (single hardcoded clinic config)
- Business tools (no calendar, no CRM)
- Multiple languages (Telugu only)
- Redis (sessions in memory)
- PostgreSQL (no persistence)
- Admin UI
- Monitoring/metrics
- Error recovery / circuit breakers
