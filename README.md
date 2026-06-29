# Voice Agent

India-first, multilingual voice AI agent for telephony. Streams a phone call
through **Sarvam** STT → LLM → TTS with a non-blocking turn-taking and
**barge-in** engine (the caller can interrupt the agent mid-sentence).

## Architecture

```
Telnyx media WS ──► audio_reader ──► STT ──┐
 (PCMU 8kHz)                                │ VAD + transcript events
                                            ▼
                                      TurnEngine (FSM)
                                  LISTENING → THINKING → SPEAKING
                                            │   ▲ barge-in cancels the turn
                                            ▼   │
                              LLM (stream) → TTS (stream) → playback pump
Telnyx media WS ◄──────────────── paced 20ms PCMU frames ◄──┘
```

- **`src/main.py`** — FastAPI app: Telnyx webhook + `/media-stream` WebSocket glue.
- **`src/pipeline/turn_engine.py`** — the brain. Each user turn runs as a
  cancellable task; barge-in cancels it and runs one centralised interrupt
  (cancel LLM → flush playback → truncate history to what was *actually played*).
  Playback is paced at real time and **pausable**, so the moment VAD detects
  caller speech the agent goes silent within ~one frame — then the guard stack
  decides whether to stay stopped or resume.
- **`src/pipeline/barge_in.py`** — the guard stack. A candidate interruption is
  judged from its transcript: **hard phrases** ("stop"/"ఆగు") interrupt
  instantly; **backchannels** ("uh-huh"/"haan"/"avunu"/"సరే") and noise do *not*
  interrupt — playback resumes (false-interruption recovery). Multilingual
  (te/hi/kn/ta/en, native + romanized).
- **`src/services/{stt,llm,tts}/sarvam.py`** — streaming Sarvam clients.
- **`src/audio/codec.py`** — telephony codec + per-call stateful resampling.
- **`src/security/telnyx.py`** — Ed25519 webhook signature verification.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # then fill in SARVAM_API_KEY, TELNYX_API_KEY, PUBLIC_URL
```

## Run

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8000
```

Point your Telnyx Call Control application's webhook at
`https://<your-host>/webhook/telnyx` and set `PUBLIC_URL` to the same host.

## Tests

```bash
pytest
```

The suite covers codec round-trips, webhook signature verification, and — most
importantly — that a barge-in mid-speech cancels the turn and truncates history.

## Configuration

All settings load from `.env` (see `.env.example`). Notable knobs:

| Setting | Default | Purpose |
|---|---|---|
| `DEFAULT_LANGUAGE` | `te-IN` | STT/TTS language |
| `STT_BUFFER_MS` | `100` | Lower = snappier barge-in, more WS traffic |
| `BARGEIN_MIN_WORDS` | `2` | Min words before a candidate counts as a real interruption |
| `BARGEIN_FALSE_TIMEOUT_MS` | `1200` | VAD fired but no real words within this → resume |
| `BARGEIN_ENABLE_RECOVERY` | `true` | Pause-then-resume for backchannels/noise |
| `ENABLE_FILLERS` | `false` | Play "hmm" filler while LLM thinks (see note) |
| `TELNYX_PUBLIC_KEY` | — | When set, webhooks are signature-verified |
| `SYSTEM_PROMPT` / `GREETING_TEXT` | jewellery demo | Agent persona |

> **Fillers** are wired but **off by default**: the bundled `assets/fillers/*.raw`
> clips need format verification (PCM16 vs mu-law) on a live call before enabling
> them in the audio path.

## Notes

- Inbound/outbound telephony audio is **mu-law (PCMU)**; the engine decodes with
  `ulaw2lin` and emits paced 20 ms mu-law frames.
- `audioop` was removed from the Python 3.13 stdlib; the `audioop-lts` backport is
  pulled in automatically on 3.13+.
