# Deploy SonusLabs to the Raspberry Pi 5 → https://sonuslabs.online

Permanent URL via a Cloudflare **named** tunnel (never expires, survives reboots),
Caddy reverse proxy, systemd. Run everything on the Pi as user `likhith`.

Architecture: `user → Cloudflare edge (TLS) → named tunnel → cloudflared → Caddy :80 → app :8001`.
The app (FastAPI/uvicorn) serves both the API and the built React site on one origin.

---

## Step 0 — put sonuslabs.online on Cloudflare (one time, required)

A named tunnel can only route a domain whose **DNS is managed by Cloudflare**.

1. Cloudflare dashboard → **Add a site** → `sonuslabs.online` (Free plan is fine).
2. Cloudflare gives you **2 nameservers**. Go to your registrar (where you bought
   sonuslabs.online) and **replace the nameservers** with Cloudflare's.
3. Wait until the domain shows **Active** in Cloudflare (minutes to a few hours).

Check it's ready: `dig NS sonuslabs.online +short` should show `*.ns.cloudflare.com`.
If it doesn't, `cloudflared tunnel login` later won't list the domain.

---

## Step 1 — get the code + run the setup script

```bash
cd ~
git clone https://github.com/likhith05072002/voice-agents.git sonuslabs
cd sonuslabs
bash deploy/setup_pi.sh
```

The script (idempotent — safe to re-run) installs Python + deps, builds the
frontend, installs Caddy (adding our block without touching your other apps),
installs cloudflared, applies low-latency knobs, and registers the systemd
service. It stops before the interactive steps below.

---

## Step 2 — fill secrets + start the app

```bash
nano .env.production        # set SARVAM_API_KEY and OPENROUTER_API_KEY (rest has good defaults)
sudo systemctl start voice-agent

# verify locally (before touching the tunnel):
curl -s localhost:8001/health                          # -> {"status":"ok",...}
curl -s -H 'Host: sonuslabs.online' localhost/health   # -> same, proves Caddy routes
```

If health is OK on both, the Pi side is done. If not: `journalctl -u voice-agent -n 50`.

---

## Step 3 — create the named tunnel (one time)

```bash
cloudflared tunnel login                              # opens a browser; authorise sonuslabs.online
cloudflared tunnel create sonuslabs                   # prints a TUNNEL-ID + creds json path
cloudflared tunnel route dns sonuslabs sonuslabs.online
cloudflared tunnel route dns sonuslabs www.sonuslabs.online

sudo mkdir -p /etc/cloudflared
sudo cp deploy/cloudflared-config.yml /etc/cloudflared/config.yml
sudo nano /etc/cloudflared/config.yml                 # replace <TUNNEL-ID> in credentials-file with the real id

sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

---

## Step 4 — open it

**https://sonuslabs.online** — the landing page, the demo orb, `/create`, `/console`.
It's now permanent: survives reboots (both `voice-agent` and `cloudflared` are
enabled services) and the URL never changes.

---

## Updating later (after you push new code)

```bash
cd ~/sonuslabs
git pull
.venv/bin/pip install -q -r requirements.txt          # only if deps changed
( cd sonuslabs-web && npm ci && npm run build )        # only if frontend changed
sudo systemctl restart voice-agent
```

## Phone calls (optional, later)

The web demo needs nothing else. For real phone numbers: fill the `TELNYX_*`
vars in `.env.production`, restart, and point the Telnyx app's webhook at
`https://sonuslabs.online/webhook/telnyx` (one time). India numbers use a
different provider — revisit telephony pricing then (see the cost notes).

## Troubleshooting

| Symptom | Check |
|---|---|
| `cloudflared tunnel login` doesn't list the domain | Step 0 not Active yet (`dig NS sonuslabs.online +short`) |
| 502 at sonuslabs.online | app down → `sudo systemctl status voice-agent`; or Caddy → `sudo systemctl status caddy` |
| Site loads but orb won't connect | WebSocket blocked — confirm `keepAliveTimeout` in config.yml; Cloudflare proxy (orange cloud) must be ON |
| High latency | `cpufreq-info` should say `performance`; the Pi's home uplink is the floor |
| Logs | app: `journalctl -u voice-agent -f` · tunnel: `journalctl -u cloudflared -f` · caddy: `journalctl -u caddy -f` |

## Skip Caddy? (alternative)

Caddy is optional with a tunnel. To go tunnel→app directly: in
`/etc/cloudflared/config.yml` change both `service:` lines from
`http://127.0.0.1:80` to `http://127.0.0.1:8001`, and skip the Caddy parts.
Caddy is kept because it makes hosting your other apps (career-aura, jobs4u…)
on the same Pi/tunnel a one-block-per-app affair.
