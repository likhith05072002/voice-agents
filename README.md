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
- **`src/services/{stt,llm,tts}/sarvam.py`** — streaming Sarvam clients
  (connect with bounded backoff retry; LLM disables Sarvam "thinking" via
  `reasoning_effort: null` to keep TTFT ~340 ms instead of ~8 s).
- **`src/pipeline/endpointing.py`** — semantic endpointing: flags a final
  transcript that looks unfinished (trailing conjunction / dangling comma) so
  the engine can merge it with the caller's next final (opt-in).
- **`src/observability/metrics.py`** — `TurnLatency`: per-turn stage breakdown
  (`stt_endpoint → llm_ttft → tts_ttfa → first_frame`) emitted as `turn.latency`.
- **`src/audio/dsp.py`** — inbound DSP: noise gate + AGC + NLMS echo canceller
  (with Geigel double-talk detection) + **spectral noise suppression**
  (`SpectralDenoiser`: streaming STFT with sqrt-Hann COLA windows and an
  adaptive noise profile — removes steady background noise from *under* the
  caller's speech; measured **+8.3 dB SNR at 0.06 ms/frame**). Enable per agent
  with `enable_noise_suppression`. For neural-grade suppression plus audio-only
  turn prediction, the commercial upgrade path is the Krisp VIVA SDK (license
  required) — this class is the drop-in point.
- **`src/agent/`** — tool/function calling: a `ToolRegistry`, a tool-resolution
  runner, and a demo registry (gold price + shop hours).
- **`src/safety/guard.py`** — trust-boundary guards: prompt-injection flagging +
  a per-sentence system-prompt-leak blocker (refusal).
- **`src/pipeline/history.py`** — bounded conversation context (`select_context`
  + `prune`) that never orphans a tool result.
- **`src/tenancy/`** — **multi-tenant foundation**. `AgentConfig` is one
  business (persona, voice, language, flags, DIDs); `AgentStore` resolves a call
  to an agent by dialed number or id; `CallRegistry` bridges the webhook→media
  handoff; `factory.build_engine` builds a `TurnEngine` from an `AgentConfig`.
  Each agent declares its own `tool_sets` (resolved via `src/agent/catalog.py`)
  and inline `knowledge_docs`, so businesses get distinct tools/knowledge/voice
  from one deployment — see `data/agents.example.json`.
- **`src/integrations/webhooks.py`** — per-agent **event webhooks**: POSTs the
  call record (+summary) to a business's `webhook_url` on completion,
  HMAC-SHA256 signed, best-effort (never affects the call).
- **`src/telephony/telnyx.py`** — Telnyx Call Control client (answer, stream,
  hangup, **create_call** for outbound), testable with a mocked transport.
- **`src/persistence/`** — durable **call records**. `CallRecorder` collects the
  transcript + per-turn latency during a call (no hot-path I/O); a pluggable
  `CallStore` (SQLite default with WAL, in-memory for tests) persists them at
  call end. `GET /calls?agent_id=&limit=` lists records; `GET /analytics?agent_id=&since=`
  aggregates per-business volume, avg duration, avg perceived latency, and
  outcome breakdown (dashboards / billing). With `ENABLE_CALL_SUMMARY`, each call
  gets an LLM summary + outcome + sentiment (`src/persistence/summary.py`).
- **`src/util/backoff.py`** — deterministic async retry/backoff utility.
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

### Onboard a business at runtime (admin API)

```bash
curl -X POST https://<your-host>/agents -H "x-api-key: $ADMIN_API_KEY" \
  -H "content-type: application/json" \
  -d '{"agent_id": "downtown-dental", "name": "Downtown Dental",
       "language": "en-IN", "voice": "meera", "phone_numbers": ["+12125550100"],
       "system_prompt": "You are Maya at Downtown Dental...",
       "greeting_text": "Thanks for calling Downtown Dental..."}'
```

`GET/PATCH/DELETE /agents/{id}` round out CRUD. Agents persist to SQLite
(`AGENTS_DB_PATH`) and hydrate on boot — no redeploy to add a business. All
mutations require `x-api-key: $ADMIN_API_KEY`.

### Receptionist features

- **Human handoff**: give an agent `transfer_numbers` (e.g. `{"owner": "+91..."}`)
  and the LLM gets a `transfer_call` tool. It speaks a confirmation, and the
  Telnyx transfer executes only **after** the audio has fully played
  (`src/agent/transfer.py` + the engine's deferred-action hook).
- **Batch campaigns**: `POST /outbound/batch` `{calls: [{to, context}...],
  agent_id, pace_per_min}` places paced calls (one bad number never kills the
  campaign); poll `GET /outbound/batch/{id}` for progress.
- **Transcript search**: `GET /calls/search?q=refund&agent_id=` — keyword search
  over stored transcripts (Unicode-safe, LIKE-escaped).
- **Per-agent LLM**: `llm_model` / `llm_reasoning_effort` on the agent config
  override the platform default per business.

### Outbound calls

```bash
curl -X POST https://<your-host>/outbound \
  -H "x-api-key: $OUTBOUND_API_KEY" -H "content-type: application/json" \
  -d '{"to": "+15551112222", "agent_id": "downtown-dental",
       "context": {"customer": "Asha", "reason": "appointment reminder"}}'
```

Requires `TELNYX_CONNECTION_ID` + `TELNYX_FROM_NUMBER`. The chosen agent and the
`context` (injected into the prompt) are pre-registered; when the callee answers,
the same media pipeline runs. Protect the endpoint with `OUTBOUND_API_KEY` and
throttle with `MAX_OUTBOUND_PER_MIN`.

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
| `AGENTS_FILE` | — | JSON array of per-business agents (multi-tenant); empty = single default agent from the persona settings |
| `DEFAULT_LANGUAGE` | `te-IN` | STT/TTS language (default agent) |
| `STT_BUFFER_MS` | `100` | Lower = snappier barge-in, more WS traffic |
| `STT_HIGH_VAD_SENSITIVITY` | `true` | Faster speech-onset, but more false triggers; turn off if the agent stutters/self-interrupts in noise |
| `BARGEIN_MIN_WORDS` | `2` | Min words before a candidate counts as a real interruption |
| `BARGEIN_FALSE_TIMEOUT_MS` | `1200` | VAD fired but no real words within this → resume |
| `BARGEIN_SPEECH_END_GRACE_MS` | `300` | After the caller's blip ENDS with no transcript, resume in this long instead of the full false-timeout (anti-stutter) |
| `BARGEIN_ENABLE_RECOVERY` | `true` | Pause-then-resume for backchannels/noise |
| `ENABLE_SMART_ENDPOINTING` | `false` | Hold an unfinished final and merge with the next (needs live A/B before enabling) |
| `ENDPOINTING_CONTINUATION_MS` | `600` | How long to wait for the continuation before firing the buffered fragment |
| `SARVAM_LLM_MODEL` | `sarvam-30b` | Chat model (sarvam-m is deprecated) |
| `SARVAM_LLM_REASONING_EFFORT` | `null` (off) | `null` disables thinking (~340 ms TTFT); `low`/`medium`/`high` enable it (slower) |
| `EAGERNESS` | `balanced` | One dial for barge-in/endpointing: `cautious`/`balanced`/`eager` |
| `ENABLE_LANGUAGE_SWITCH` | `true` | TTS voice language follows the caller's language |
| `ENABLE_RAG` | `false` | Inject relevant shop-knowledge snippets into context |
| `MAX_CONCURRENT_SESSIONS` | `100` | Reject new media streams past this |
| `MAX_TURNS_PER_MIN` | `0` | Per-call turn rate cap (0 = off) |
| `ENABLE_INPUT_DSP` | `true` | Noise gate + AGC clean/level caller audio before STT |
| `ENABLE_ECHO_CANCELLATION` | `false` | NLMS echo canceller (stops self-interrupts; needs live delay tuning) |
| `ENABLE_TOOLS` | `false` | Function calling (demo: gold price + shop hours) |
| `ENABLE_SAFETY` | `true` | Prompt-injection flagging + system-prompt-leak blocking |
| `ENABLE_IDLE` | `true` | Re-prompt then hang up on a silent caller |
| `IDLE_REPROMPT_MS` / `IDLE_HANGUP_MS` | `10000` / `30000` | Silence thresholds |
| `ENABLE_FILLERS` | `false` | Play "hmm" filler while LLM thinks (see note) |
| `TELNYX_PUBLIC_KEY` | — | When set, webhooks are signature-verified |
| `SYSTEM_PROMPT` / `GREETING_TEXT` | jewellery demo | Agent persona |

> **Fillers** are wired but **off by default**: the bundled `assets/fillers/*.raw`
> clips need format verification (PCM16 vs mu-law) on a live call before enabling
> them in the audio path.

## Performance & observability

**Measure the number that matters.** Every turn emits a `turn.latency` log with
the full breakdown — the headline is `perceived_ms` (caller stops → first reply
frame out):

```
turn.latency  turn_id=7  stt_endpoint_ms=210  llm_ttft_ms=350  tts_ttfa_ms=300  tts_to_frame_ms=8  perceived_ms=870
```

Grep a call log for `turn.latency` to see where the time goes; if `stt_endpoint_ms`
dominates it's Sarvam endpointing, if `llm_ttft_ms` is high check
`SARVAM_LLM_REASONING_EFFORT`.

**Repeatable micro-benchmarks** (network-free — pacing, first-audio chunking,
engine overhead, **barge-in latency**, **concurrency stress**):

```bash
python scripts/bench_pipeline.py
```

Representative results (one machine, fakes — isolates the engine from network):

| Metric | Result |
|---|---|
| Playback pacing | **98% of real-time** (vs 154% with a naive per-frame `sleep`) |
| Barge-in: VAD onset → agent silent | **~16 ms** (≈ one 20 ms frame; target <100 ms) |
| First-audio chunk start | **38 chars sooner** (clause-early vs sentence-only) |
| Engine compute / turn | **~0.1 ms** |
| Concurrency | **50 simultaneous sessions, 0 errors** |

Latency engineering applied (all in `turn_engine.py` / service clients): absolute-
deadline playback pacing; reasoning disabled for ~340 ms LLM TTFT; first chunk
flushed at a clause boundary; **TTS language-switch overlapped with LLM
generation** (hidden under TTFT, not added to it); **LLM connection pre-warmed
during the greeting** so turn 1 skips client construction + TLS handshake;
**tool turns speak the tool-decision completion directly** instead of making a
second streaming call (one full LLM round-trip saved per tool-enabled turn);
pump skips the gate `await` when playing (scales across many concurrent streams).

> The remaining end-to-end latency is dominated by **provider stages** (Sarvam STT
> endpointing, LLM TTFT, TTS TTFA) and the **8 kHz telephony line** — measure a
> live call's `turn.latency` to see which dominates before tuning further.

## Notes

- Inbound/outbound telephony audio is **mu-law (PCMU)**; the engine decodes with
  `ulaw2lin` and emits paced 20 ms mu-law frames.
- `audioop` was removed from the Python 3.13 stdlib; the `audioop-lts` backport is
  pulled in automatically on 3.13+.
