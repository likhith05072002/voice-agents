# Research: World-Class Barge-In & Turn-Taking for Voice AI

## Synthesized from 30+ papers, production architectures, and engineering blogs

---

## 1. WHY OUR CURRENT SYSTEM FEELS UNNATURAL

Our system is **half-duplex sequential**: user talks → wait → bot talks → wait → repeat.
Each turn takes ~6.5 seconds (LLM ~450ms + TTS connection ~3-4s + TTS synthesis ~2s).

**Human conversation is fundamentally different:**
- Turn gaps average **200ms** (not 6 seconds)
- Speakers predict turn boundaries BEFORE the other person finishes
- Backchannels ("uh-huh", "hmm") happen DURING the other's speech
- Interruptions are instant — the interrupted party stops within 200ms
- Overlap (both talking) is NORMAL — accounts for 40%+ of natural turns

---

## 2. THE THREE-LAYER TURN DETECTION ARCHITECTURE (Production Standard 2026)

**Layer 1: Voice Activity Detection (VAD)**
- Silero VAD: 30ms frame analysis, speech probability 0-1
- Detects: speech start, speech end (silence onset)
- Latency: ~30ms
- Limitation: Can't distinguish backchannel from interruption

**Layer 2: STT Endpointing**
- Sarvam STT `high_vad_sensitivity: true` gives END_SPEECH events
- Semantic endpointing: detects complete utterances (not just silence)
- Latency: 150-300ms
- Advantage: Fires before trailing silence ends

**Layer 3: Semantic Turn Classifier (Optional, highest accuracy)**
- Neural model analyzing partial transcript + prosody + context
- Distinguishes: backchannel vs interruption vs continued silence
- Latency: 50-160ms
- Examples: LiveKit Turn Detector v1, Deepgram Flux, Krisp v3

**Our implementation plan: Use Layer 1 + Layer 2 (Sarvam provides both)**

---

## 3. BARGE-IN: THE CRITICAL PATH

When user speaks while bot is talking, we must:

### Detection (<100ms)
- VAD detects speech onset during bot playback
- Classification: is this a real interruption or backchannel?
- Simple heuristic: sustained speech >300ms = interruption, <300ms = backchannel

### Cancellation (<50ms after detection)
1. **Stop TTS synthesis** — close WebSocket or send cancel
2. **Flush audio buffer** — don't play queued audio
3. **Cancel LLM generation** — stop producing tokens
4. **Track what was actually played** — for conversation context

### Recovery (<200ms)
1. Add ONLY the played portion to conversation history
2. Resume listening for user's new input
3. Process new STT transcript
4. Generate new response

### Total barge-in latency target: <350ms

---

## 4. THE STREAMING OVERLAP ARCHITECTURE (Key to Sub-1s Response)

**Current (Sequential — 6.5s per turn):**
```
[User stops] → [STT final] → [LLM generate all] → [Open TTS] → [TTS synthesize all] → [Send all]
                                                     ^^^^^^^^^^^
                                                     3-4s wasted on connection
```

**Target (Streaming Overlap — sub-1s):**
```
[User stops] → [STT partial] → [LLM token 1] → [TTS chunk 1] → [Play chunk 1]
                                [LLM token 2] → [TTS chunk 2] → [Play chunk 2]
                                [LLM token 3] → [TTS chunk 3] → [Play chunk 3]
                                ...all overlapping, not sequential
```

**Key optimizations:**
1. **Pre-opened TTS connection** — open at call start, reuse for all responses
2. **Sentence-level streaming** — pipe first LLM sentence to TTS while LLM continues
3. **No waiting for full response** — TTS starts on first sentence

**Estimated latency with overlap:**
- STT endpointing: 200ms
- LLM first sentence: 400ms (but starts during STT)
- TTS first audio chunk: 300ms (but starts during LLM)
- **Overlap saves: ~400ms**
- **Total: ~700ms** (vs 6.5s current)

---

## 5. CONNECTION POOLING (Eliminates 3-4s TTS Connection Overhead)

**Current problem:** Opening fresh TTS WebSocket per response = 3-4 seconds overhead.

**Solution: TTS Connection Pool**
- Open 1-2 TTS WebSocket connections at call start
- Reuse across responses (send config once, text multiple times)
- If connection dies, reconnect in background
- Pool warms during greeting playback

**Why our TTS connection reuse failed earlier:**
- The `get_audio()` method uses a queue that gets `None` on completion
- After first response, the receive loop has ended
- Fix: Keep receive loop running, signal completion differently

---

## 6. BACKCHANNEL HANDLING

**Problem:** User says "uh-huh" while bot talks → bot shouldn't stop

**Detection heuristics (no ML needed):**
1. Speech duration < 500ms
2. Low energy (quieter than normal speech)
3. Known tokens: "hmm", "uh-huh", "yeah", "okay", "haan", "avunu"
4. Occurs during bot's speech, not after a pause

**Handling:**
- Don't stop TTS playback
- Don't trigger new LLM response
- Optionally: inject brief acknowledgment audio ("hmm") without interrupting flow

---

## 7. PRODUCTION DESIGN FOR OUR SYSTEM

### Architecture
```
                     ┌─────────────────────────────────────┐
                     │         CALL SESSION                │
                     │                                     │
  Telnyx WS ──────►  │  Audio In ──► STT (streaming)      │
  (PCMA 8kHz)        │                    │                │
                     │              VAD events             │
                     │              Transcripts            │
                     │                    │                │
                     │              Turn Detector          │
                     │              (VAD + STT endpoint)   │
                     │                    │                │
                     │         ┌──────────┴──────────┐    │
                     │         │                     │    │
                     │    [Bot speaking?]        [Bot idle?]│
                     │         │                     │    │
                     │    Barge-in check         Start LLM │
                     │    (duration>300ms?)      streaming  │
                     │         │                     │    │
                     │    Cancel TTS             Sentence   │
                     │    Start new turn         Buffer     │
                     │                               │    │
                     │                          TTS Pool   │
                     │                          (pre-opened)│
                     │                               │    │
  Telnyx WS ◄──────  │  Audio Out ◄── PCMU encode ◄──┘    │
  (PCMU)             │                                     │
                     └─────────────────────────────────────┘
```

### State Machine
```
LISTENING ──(STT transcript + silence)──► PROCESSING
PROCESSING ──(LLM first sentence)──► SPEAKING
SPEAKING ──(TTS complete)──► LISTENING
SPEAKING ──(barge-in detected)──► CANCELLING ──► LISTENING
```

### Key Implementation Changes Needed

1. **TTS Connection Pool** — open at call start, reuse
2. **Streaming LLM→TTS** — pipe sentences as they come
3. **Barge-in detector** — check speech during SPEAKING state
4. **Audio buffer management** — track what was played
5. **Conversation history tracking** — append only played text

---

## 8. LATENCY BUDGET (Target)

| Stage | Current | Target | How |
|-------|---------|--------|-----|
| STT endpoint | 200ms | 200ms | Already good |
| LLM TTFT | 400ms | 400ms | Already good |
| LLM first sentence | 450ms | 450ms | Already good |
| TTS connection | 3-4s | 0ms | Connection pool |
| TTS first audio | 300ms | 300ms | Already good |
| Total | 6500ms | **950ms** | Overlap + pool |
| With overlap | - | **~700ms** | LLM and TTS overlap |

---

## 9. RESEARCH SOURCES

### Papers
1. τ-Voice: Benchmarking Full-Duplex Voice Agents (arXiv 2603.13686)
2. Phoenix-VAD: Streaming Semantic Endpoint Detection (arXiv 2509.20410)
3. Semantic-Aware Interruption Detection (arXiv 2603.24144)
4. IHBench: Post-Interruption Recovery (arXiv 2606.19595)
5. Full-Duplex-Bench v2 (arXiv 2507.23159)
6. SALM-Duplex: Direct Duplex Modeling (arXiv 2505.15670)
7. Prompt-Guided Turn-Taking Prediction (arXiv 2506.21191)
8. Speculative End-Turn Detector (arXiv 2503.23439)
9. Multilingual Turn-taking with VAP (arXiv 2403.06487)
10. Thai Semantic EOT Detection (arXiv 2510.04016)
11. Toward Low-Latency Voice Agents for Telecom (arXiv 2508.04721)
12. ChipChat: Low-Latency Cascaded Agent (arXiv 2509.00078)

### Engineering
- LiveKit: Turn Detection blog + docs
- Deepgram: Flux architecture + state machine
- AssemblyAI: Universal-3 Pro semantic endpointing
- Krisp: Turn prediction + interruption detection
- Vapi: Pipeline architecture parts 1 & 2
- ElevenLabs: Latency optimization blog
- Cartesia: SSM-based TTS architecture
- Vatsal Shah: Voice AI Agents 2026 guide
- Nick Tikhonov: Sub-500ms voice agent from scratch

### Benchmarks
- Human turn gap: 200ms modal (psycholinguistic research)
- Silero VAD: 380-520ms P95
- Production voice agents: 500-800ms (good), 800-1200ms (acceptable)
- Backchannel detection F1: 0.918
