# Build the SonusLabs.ai frontend — complete prompt

Copy everything below this line into Claude (or any frontend-focused agent).

---

You are building the complete production frontend for **SonusLabs** (sonuslabs.ai) — a commercial multilingual voice-AI platform from India. The backend is FINISHED and running; you are building a React app against its real API. Do not mock anything that the API provides.

## The product in one paragraph

SonusLabs gives any business a human-sounding AI phone receptionist that speaks **English, Hindi, Kannada, Telugu and Tamil — and switches language mid-call when the caller does**. It answers on a real phone number or an embeddable web-call widget, books appointments, quotes live prices (e.g. today's real gold rate for a jeweller), searches the web for current facts, handles interruptions like a human (stops instantly, never talks over the caller, never loses its place), and hands unclear cases to staff. The killer onboarding: paste your website URL, SonusLabs researches your business and builds your agent's persona + knowledge automatically — you're talking to your own receptionist in under two minutes.

## Positioning & audience

- Primary buyers: Indian SMBs (jewellers, clinics, cafes, salons, real estate) + tech companies wanting a B2B receptionist. Non-technical owners — the UI must never require reading docs.
- Competitors to study for register (not to copy): ElevenLabs Agents (polish, trust), rumik.ai (the instant "talk to Ira" orb), Vapi/Retell/Bland (developer clarity, transparent per-minute pricing). SonusLabs' wedge vs all of them: **true Indian-language fluency and India-market data (live Indian gold rates, IST awareness), at Indian prices (~₹2/min all-in)**.
- Brand voice: confident, warm, a little proud of being Indian-built. No corporate mush. Tagline direction: "The receptionist who never sleeps — in five languages." / "आपकी आवाज़, हमारा एआई."

## Design direction

- **Distinctive, not generic-SaaS.** Take rumik.ai's warmth (paper-texture, hand-crafted feel) as the mood but execute with ElevenLabs-grade precision. Suggested system: warm off-white paper background (#FAF7F0-ish) with subtle dot grid, ink-black type, ONE saturated accent (deep marigold or peacock teal — pick and commit), generous whitespace, soft-radius cards, tasteful micro-motion (Framer Motion). Dark mode for the console/dashboard area is fine; the marketing pages stay light and warm.
- Typography: a characterful display face for headlines (e.g. a modern serif or rounded grotesk) + clean UI sans. Devanagari/Kannada glyph support matters — test headlines in हिंदी/ಕನ್ನಡ.
- The signature visual: **the Call Orb** — a breathing circle that becomes a live waveform when the agent speaks and pulses to mic level when the user speaks (like rumik's "connecting" circle but richer). It appears in the hero, in onboarding step 4, and in every agent's detail page.

## Tech stack (do it exactly this way)

- React 18 + Vite + TypeScript. Tailwind CSS. Framer Motion for motion. Zustand or React Query for data. React Router.
- No backend of your own, no auth for v1 (single-tenant console). API base URL from `VITE_API_BASE` env (default `http://localhost:8001`).
- Web Audio implementation notes below are from the WORKING reference client — follow them exactly, they encode hard-won fixes.

## Pages & flows

### 1. Landing page (marketing)
- Hero: headline + subline + **the Call Orb with "Talk to our receptionist" button** — clicking starts a REAL web call to the demo agent (agent_id `sonuslabs`) right in the hero, with live captions of both sides appearing beneath the orb (user line + agent line, chat-bubble style, exactly like a call transcript). This is the whole pitch in 10 seconds. Mic permission → orb goes live → talk.
- Sections: (a) "Paste your website. Meet your receptionist." — onboarding teaser with a URL input that jumps into the onboarding wizard; (b) language showcase — five language chips that play a short sample line each (`GET /voice-sample/{voice}` returns WAV; render a small audio player styled as a chip); (c) how it works — 3 steps with tasteful illustration; (d) live-data credibility strip — "Quotes today's real gold rate. Knows today's date. Searches the live web."; (e) interruption demo blurb — "Talk over it. It stops. Like a person."; (f) pricing; (g) footer.
- Pricing section: simple honest cards — Starter (pay-as-you-go ₹X/min), Business (bundle minutes + priority voices), Enterprise (custom + on-prem). Use placeholder numbers marked clearly as launch pricing.

### 2. Onboarding wizard ("Create your receptionist") — the wow flow
Route `/create`. Four steps, one screen each, progress dots:
1. **Website** — URL input + optional "describe how it should behave" textarea. Submit → `POST /onboard/research` `{website_url, description}`. Show a delightful research animation (the orb "reading" the site; stream witty status lines: "reading your website… asking around about you… writing her script…"). Takes 15–60s; handle 422/500 with a friendly retry.
2. **Review the draft** — the response is a full draft agent: editable fields for `name`, `greeting_text`, `language` (dropdown: en-IN/hi-IN/kn-IN/te-IN/ta-IN), `voice` (dropdown fed by `GET /voice-lab` with inline play via `/voice-sample/{voice}`), the `knowledge_docs` array as editable fact cards (add/remove/edit), and the `system_prompt` behind an "advanced" accordion.
3. **Create** — `POST /agents` with the (edited) draft body. Handle 409 (id exists) by suffixing the agent_id.
4. **Meet her** — the Call Orb, connected via web call to the NEW agent. Live dual captions. Confetti-level moment, restrained execution. CTA after first call: "Get her a phone number →" (contact/waitlist form for now).

### 3. Console (dashboard) — route `/console`
Dark, dense, professional. Left nav: Agents, Calls, Live, Voice Lab, Analytics.
- **Agents**: card grid from `GET /agents-lite` (id, name); click → agent detail: full config via `GET /agents/{id}`, editable form → `PATCH /agents/{id}`, delete with confirm → `DELETE /agents/{id}`, plus an embedded Call Orb to talk to this agent instantly, and "Test call my phone": `POST /test/call-me` `{to:"+91...", agent_id}`.
- **Calls**: table from `GET /calls?limit=50` (columns: time, agent, duration_s, turn_count, avg_perceived_ms, outcome). Row expands to full turn-by-turn transcript (the `turns` field is a JSON string — parse it). Search box → `GET /calls/search?q=`.
- **Live**: poll `GET /live-transcript?since={unix_ts}` every 600ms → rolling feed of `{t, call_id, role, text}` lines across all active calls; `active` count in the response drives a LIVE badge.
- **Voice Lab**: grid of `GET /voice-lab` voices, each with a styled player for `/voice-sample/{voice}` and a "set as default for agent…" action (PATCH the agent's `voice`).
- **Analytics**: `GET /analytics` — render whatever keys come back (calls count, avg latency, outcomes) as stat cards + a simple bar/line chart.
- **Test runner** (nice-to-have tab): `GET /test/scenarios` list → `POST /test/start` `{scenario, transport:"loopback"}` → poll `GET /test/status/{test_id}` rendering the steps table (question, latency_ms, time_to_silence_ms, answer, check pass/fail) → audio player at `GET /test/audio/{test_id}` when done.

## The web-call client (CRITICAL — follow exactly)

WebSocket `ws(s)://{API_HOST}/web-call?agent_id={id}`, binaryType `arraybuffer`.

**Uplink (mic → server):** capture with `getUserMedia({audio:{echoCancellation:true, noiseSuppression:true, autoGainControl:true}})`. Downsample the AudioContext rate (usually 48k) to **16 kHz mono PCM16** and send raw Int16 ArrayBuffers continuously (~every 85ms is fine). Downsample by **box-averaging each window** — naive sample-picking aliases noise and corrupts transcription:
```js
const ratio = ctx.sampleRate / 16000;
for (let i = 0; i < out.length; i++) {
  const a = Math.floor(i*ratio), b = Math.floor((i+1)*ratio);
  let s = 0; for (let k = a; k < b; k++) s += inp[k];
  out[i] = Math.max(-32768, Math.min(32767, (s/(b-a)) * 32768));
}
```

**Downlink (server → speakers):** binary frames are raw **PCM16 mono 16 kHz** (batched ~80ms). Schedule gaplessly with SAMPLE-COUNT-derived start times (accumulating float durations drifts and clicks) and a 250ms jitter cushion:
```js
let t = epoch + totalSamples / 16000;
if (t < ctx.currentTime + 0.02) { epoch = ctx.currentTime + 0.25; totalSamples = 0; t = epoch; }
src.start(t); totalSamples += i16.length;
```

**Text frames (same socket):** JSON `{role: "user"|"assistant", text: string}` — render as the live captions (user bubbles left/amber, agent bubbles right/accent). The first assistant text is the greeting.

Interruption UX: just talk — the backend handles barge-in. Reflect state on the orb: agent-speaking = waveform animation, user-speaking = pulse ring, idle = slow breathing.

## Full API reference (backend is live; no auth headers needed in v1)

| Method | Path | Body / params | Returns |
|---|---|---|---|
| GET | /health | — | `{status, active_sessions, agents}` |
| GET | /agents-lite | — | `{agents:[{agent_id,name}]}` |
| GET | /agents | — | `{agents:[AgentConfig]}` |
| GET | /agents/{id} | — | AgentConfig |
| POST | /agents | AgentConfig (agent_id required) | 201 AgentConfig / 409 |
| PATCH | /agents/{id} | partial AgentConfig | AgentConfig |
| DELETE | /agents/{id} | — | `{deleted}` |
| POST | /onboard/research | `{website_url, description?}` | draft AgentConfig (+`industry`) / 422 |
| GET | /voice-lab | — | `{voices:[string]}` |
| GET | /voice-sample/{voice} | — | audio/wav |
| GET | /calls | `?limit=` | `{calls:[{call_id,agent_id,from_number,to_number,started_at,ended_at,duration_s,turn_count,avg_perceived_ms,outcome,turns:"[json]"}]}` |
| GET | /calls/search | `?q=` | same shape |
| GET | /analytics | — | stats object |
| GET | /live-transcript | `?since=unix_ts` | `{lines:[{t,call_id,role,text}], active}` |
| POST | /test/call-me | `{to:"+91...", agent_id}` | `{status}` / `{error}` |
| GET | /test/scenarios | — | `{scenarios:[string]}` |
| POST | /test/start | `{scenario, transport:"loopback"\|"pstn"}` | `{test_id}` |
| GET | /test/status/{id} | — | `{state, greeting_ms, steps:[{question,latency_ms,time_to_silence_ms,answer,check}], events}` |
| GET | /test/audio/{id} | — | audio/wav |
| WS | /web-call?agent_id= | binary PCM16-16k up / PCM16-16k + JSON down | live call |
| WS | /test/listen/{test_id} | — | binary PCM16-8k (test call audio) |

AgentConfig fields the UI edits: `agent_id, name, language, voice, voice_pace (0.5–2.0), system_prompt, greeting_text, knowledge_docs: string[], enable_rag, enable_tools, eagerness ("cautious"|"balanced"|"eager")`. Show `balanced` as the default and label `eager` "snappy (may interrupt more)".

## Quality bar

- Lighthouse 90+ on the landing page; instant route transitions; skeleton loaders on every fetch; empty states with personality; error states that tell the user what to do; fully responsive (owners will do this from their phone); keyboard accessible; the Devanagari/Kannada text must render beautifully.
- Ship as: Vite project, `npm run dev` works against `VITE_API_BASE=http://localhost:8001`, `npm run build` produces a static bundle. Include a README with 5-line setup.

Build the complete application. Start with the landing page hero + working Call Orb (the highest-stakes piece), then the onboarding wizard, then the console.
