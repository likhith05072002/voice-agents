#!/usr/bin/env bash
# One-shot setup for the SonusLabs voice agent on a Raspberry Pi 5
# (Raspberry Pi OS 64-bit / Debian bookworm). Run from the repo root as user
# 'likhith':   bash deploy/setup_pi.sh
#
# Idempotent: safe to re-run. Does the mechanical parts; the INTERACTIVE parts
# (cloudflared login, filling secrets in .env.production) are called out and
# also documented in deploy/DEPLOY.md.
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"
echo "== SonusLabs setup in $APP_DIR =="

echo "== 1/7 System packages =="
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-dev git curl cpufrequtils

echo "== 2/7 Python env =="
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "== 3/7 Frontend build (the backend serves it; needs Node) =="
if ! command -v node >/dev/null; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get install -y -qq nodejs
fi
( cd sonuslabs-web && npm ci --no-audit --no-fund && npm run build )

echo "== 4/7 Environment file =="
if [ ! -f .env.production ]; then
  cp deploy/env.production.example .env.production
  echo ">>> EDIT .env.production and fill SARVAM_API_KEY + OPENROUTER_API_KEY (nano .env.production)"
fi

echo "== 5/7 Low-latency system knobs =="
echo 'GOVERNOR="performance"' | sudo tee /etc/default/cpufrequtils >/dev/null
sudo systemctl restart cpufrequtils || true
sudo tee /etc/sysctl.d/99-voice-agent.conf >/dev/null <<'SYS'
net.core.rmem_max=8388608
net.core.wmem_max=8388608
net.ipv4.tcp_slow_start_after_idle=0
SYS
sudo sysctl -q -p /etc/sysctl.d/99-voice-agent.conf || true

echo "== 6/7 Caddy (reverse proxy; adds our block, keeps your other apps) =="
if ! command -v caddy >/dev/null; then
  sudo apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -qq && sudo apt-get install -y -qq caddy
fi
sudo mkdir -p /var/log/caddy
sudo touch /etc/caddy/Caddyfile
if ! grep -q "sonuslabs.online" /etc/caddy/Caddyfile; then
  echo ">>> appending SonusLabs block to /etc/caddy/Caddyfile (your other blocks are untouched)"
  echo "" | sudo tee -a /etc/caddy/Caddyfile >/dev/null
  sudo tee -a /etc/caddy/Caddyfile < deploy/Caddyfile >/dev/null
fi
sudo systemctl enable --now caddy
sudo systemctl reload caddy 2>/dev/null || sudo systemctl restart caddy

echo "== 7/7 cloudflared install + app service =="
if ! command -v cloudflared >/dev/null; then
  curl -sL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb -o /tmp/cf.deb
  sudo dpkg -i /tmp/cf.deb
fi
sudo cp deploy/voice-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable voice-agent

cat <<'NEXT'

=========================================================================
 Mechanical setup done. NOW the interactive steps (see deploy/DEPLOY.md):

 1. Fill secrets:            nano .env.production   (SARVAM_API_KEY, OPENROUTER_API_KEY)
 2. Start the app:           sudo systemctl start voice-agent
    check it locally:        curl -s localhost:8001/health   (expect {"status":"ok"...})
    check via Caddy:         curl -s -H 'Host: sonuslabs.online' localhost:80/health

 3. Cloudflare tunnel (one time):
      cloudflared tunnel login                 # opens a browser; pick sonuslabs.online
      cloudflared tunnel create sonuslabs      # note the TUNNEL-ID it prints
      cloudflared tunnel route dns sonuslabs sonuslabs.online
      cloudflared tunnel route dns sonuslabs www.sonuslabs.online
      sudo mkdir -p /etc/cloudflared
      sudo cp deploy/cloudflared-config.yml /etc/cloudflared/config.yml
      sudo nano /etc/cloudflared/config.yml    # put the real <TUNNEL-ID> in credentials-file
      sudo cloudflared service install
      sudo systemctl enable --now cloudflared

 4. Open  https://sonuslabs.online  — done. Permanent URL, survives reboots.

 (Domain must already be added to Cloudflare with its nameservers switched, or
  `cloudflared tunnel login` won't list it. See DEPLOY.md step 0.)
=========================================================================
NEXT
