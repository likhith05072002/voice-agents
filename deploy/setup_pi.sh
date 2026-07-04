#!/usr/bin/env bash
# One-shot setup for Raspberry Pi 5 (Raspberry Pi OS 64-bit / Debian bookworm).
# Run as the 'pi' user from the repo root:  bash deploy/setup_pi.sh
set -euo pipefail

echo "== 1/5 System packages =="
sudo apt-get update -qq
sudo apt-get install -y -qq python3.11-venv python3-dev git curl

echo "== 2/5 Python env =="
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "== 3/5 Environment file =="
if [ ! -f .env.production ]; then
  cp .env.example .env.production
  echo ">>> EDIT .env.production: SARVAM_API_KEY, TELNYX_API_KEY, PUBLIC_URL=https://voice.YOURDOMAIN.com, phone numbers"
fi

echo "== 4/5 cloudflared (named tunnel) =="
if ! command -v cloudflared >/dev/null; then
  curl -sL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb -o /tmp/cf.deb
  sudo dpkg -i /tmp/cf.deb
fi
echo ">>> Once per Pi:"
echo "    cloudflared tunnel login"
echo "    cloudflared tunnel create voice-agent"
echo "    cloudflared tunnel route dns voice-agent voice.YOURDOMAIN.com"
echo "    sudo mkdir -p /etc/cloudflared && sudo cp deploy/cloudflared-config.yml /etc/cloudflared/config.yml"
echo "    (edit hostname in /etc/cloudflared/config.yml)"
echo "    sudo cloudflared service install && sudo systemctl enable --now cloudflared"

echo "== 5/5 voice-agent service =="
sudo cp deploy/voice-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable voice-agent
echo ">>> After editing .env.production:  sudo systemctl start voice-agent"
echo ">>> Then point the Telnyx app webhook at https://voice.YOURDOMAIN.com/webhook/telnyx (one time, forever)."
echo "Done."
