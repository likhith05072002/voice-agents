# SonusLabs — frontend (React + Vite + TS)

The commercial face of the SonusLabs voice-AI platform. Every page is wired to
the live backend (`../` FastAPI app) — no mock data.

## Run

```bash
npm install
# backend must be running on :8001 (see ../ ; it has CORS enabled)
echo "VITE_API_BASE=http://localhost:8001" > .env   # already committed
npm run dev            # http://localhost:5173
```

Build for deploy: `npm run build` → static bundle in `dist/`.
Point `VITE_API_BASE` at the deployed backend URL (the Cloudflare tunnel / Pi).

## Pages

- **/** — landing: live "talk to it" hero (real /web-call), 11 languages, honest ₹3/min pricing.
- **/create** — onboarding wizard: website → `POST /onboard/research` → edit draft → `POST /agents` → talk.
- **/console** — agents (list/edit/delete + talk + test-call), calls + transcripts, live feed, voice lab, analytics.

## Architecture

- `src/api.ts` — every backend endpoint + types, single source of truth.
- `src/useWebCall.ts` — the audio hook: mic→16k PCM16 (box-average downsample),
  agent PCM16 playback (sample-count scheduling, 250ms jitter buffer), live captions.
  These encode hard-won backend audio fixes — do not "simplify".
- `src/components/Orb.tsx` — the signature call orb (breathes / blooms to energy).
- `src/theme.ts` — design tokens + the 11 real languages.
