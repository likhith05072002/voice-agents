#!/usr/bin/env bash
# setup_pi_postgres.sh — turn on ACCOUNTS MODE on the Pi.
#
# Idempotent: installs PostgreSQL, creates the sonuslabs DB + role, writes
# DATABASE_URL + SESSION_SECRET into .env.production (only if missing), and
# installs a daily backup cron. Safe to re-run. Migrations run automatically
# when the app boots — no manual migration step.
#
# Run from the repo on the Pi:   bash deploy/setup_pi_postgres.sh
# Then fill the Google keys in .env.production and restart the service.
set -euo pipefail

# --- locate the repo + env file (script lives in <repo>/deploy) ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$APP_DIR/.env.production"
DB_NAME="sonuslabs"
DB_USER="sonus"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!  %s\033[0m\n' "$*"; }

# --- 1. install postgresql ---
if ! command -v psql >/dev/null 2>&1; then
  say "Installing PostgreSQL (apt)…"
  sudo apt-get update -y
  sudo apt-get install -y postgresql postgresql-contrib
else
  say "PostgreSQL already installed ($(psql --version))."
fi
sudo systemctl enable --now postgresql

# Discover the port the cluster actually listens on (usually 5432; may differ
# if another cluster/service already took it).
PG_PORT="$(sudo -u postgres psql -tAc 'SHOW port;' 2>/dev/null | tr -d '[:space:]' || echo 5432)"
PG_PORT="${PG_PORT:-5432}"
say "PostgreSQL is on port $PG_PORT."

# --- 2. role + database (idempotent) ---
role_exists() { sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; }
db_exists()   { sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; }

if role_exists; then
  say "Role '$DB_USER' already exists."
  # If .env.production already has a DATABASE_URL we assume its password is the
  # live one and leave the role untouched. Otherwise we (re)set a fresh one.
  if grep -qE '^DATABASE_URL=postgres' "$ENV_FILE" 2>/dev/null; then
    DB_PASS=""   # keep existing; DATABASE_URL already written
  else
    DB_PASS="$(openssl rand -hex 24)"
    sudo -u postgres psql -c "ALTER ROLE $DB_USER WITH LOGIN PASSWORD '$DB_PASS';"
    warn "Reset password for existing role '$DB_USER' (no DATABASE_URL was set)."
  fi
else
  DB_PASS="$(openssl rand -hex 24)"
  say "Creating role '$DB_USER'…"
  sudo -u postgres psql -c "CREATE ROLE $DB_USER WITH LOGIN PASSWORD '$DB_PASS';"
fi

if db_exists; then
  say "Database '$DB_NAME' already exists."
else
  say "Creating database '$DB_NAME' owned by '$DB_USER'…"
  sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"
fi
# pgcrypto (gen_random_uuid) — migrations also create it, but ensure the role
# can, by granting on the db.
sudo -u postgres psql -d "$DB_NAME" -c "GRANT ALL ON SCHEMA public TO $DB_USER;" >/dev/null 2>&1 || true

# --- 3. write DATABASE_URL + SESSION_SECRET into .env.production (if missing) ---
touch "$ENV_FILE"
ensure_env() {  # ensure_env KEY VALUE  — appends only if KEY= not already present
  local key="$1" val="$2"
  if grep -qE "^${key}=." "$ENV_FILE"; then
    say "$key already set in .env.production (left as-is)."
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
    say "Wrote $key to .env.production."
  fi
}

if [ -n "${DB_PASS:-}" ]; then
  ensure_env DATABASE_URL "postgresql://$DB_USER:$DB_PASS@localhost:$PG_PORT/$DB_NAME"
else
  say "DATABASE_URL already present — reusing it."
fi
ensure_env SESSION_SECRET "$(openssl rand -base64 48 | tr -d '\n/+' | cut -c1-64)"
# Never let the local-dev sign-in bypass be on in prod.
ensure_env DEV_LOGIN_ENABLED "false"

# --- 4. verify the app can connect with the DSN it will use ---
DSN="$(grep -E '^DATABASE_URL=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
say "Testing connection with the configured DATABASE_URL…"
if PGCONNECT_TIMEOUT=5 psql "$DSN" -tAc 'SELECT 1;' >/dev/null 2>&1; then
  say "Connection OK ✓"
else
  warn "Could not connect with DATABASE_URL. Check pg_hba.conf allows"
  warn "password auth on 127.0.0.1, or edit DATABASE_URL in .env.production."
fi

# --- 5. daily backups (the P0: the money ledger must survive disk loss) ---
BACKUP_DIR="$APP_DIR/backups"
mkdir -p "$BACKUP_DIR"
CRON_LINE="0 3 * * * $APP_DIR/deploy/backup_db.sh >> $BACKUP_DIR/backup.log 2>&1"
if crontab -l 2>/dev/null | grep -Fq "$APP_DIR/deploy/backup_db.sh"; then
  say "Backup cron already installed."
else
  ( crontab -l 2>/dev/null; echo "$CRON_LINE" ) | crontab -
  say "Installed daily backup cron (03:00) → $BACKUP_DIR"
fi
say "Running one backup now to verify…"
bash "$APP_DIR/deploy/backup_db.sh" && say "Backup OK ✓" || warn "Backup test failed — check backup_db.sh output above."

# --- done ---
cat <<EOF

$(printf '\033[1;32m')ACCOUNTS MODE PREPPED.$(printf '\033[0m')

Still to do BY HAND in $ENV_FILE (then restart the service):
  GOOGLE_CLIENT_ID=<from Google Cloud console>
  GOOGLE_CLIENT_SECRET=<from Google Cloud console>
  ADMIN_EMAILS=likhiths05072002@gmail.com
  (leave OAUTH_REDIRECT_BASE empty — prod uses PUBLIC_URL=https://sonuslabs.online)

Then:
  cd $APP_DIR
  .venv/bin/pip install -r requirements.txt      # asyncpg, itsdangerous
  (cd sonuslabs-web && npm ci && npm run build)   # ship /docs /privacy /terms + console
  sudo systemctl restart voice-agent
  curl -s http://127.0.0.1:8011/health            # {"status":"ok"} → migrations ran on boot

Google sign-in redirect URI to register (once):
  https://sonuslabs.online/auth/google/callback
EOF
