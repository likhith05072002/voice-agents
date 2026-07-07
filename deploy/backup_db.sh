#!/usr/bin/env bash
# backup_db.sh — dump the accounts Postgres DB, gzip it, keep 14 days.
# Installed as a daily cron by setup_pi_postgres.sh. Safe to run by hand.
#
# The money ledger, wallets and payments live only in this DB. Without an
# off-box copy of these dumps you are one SD-card failure from losing them —
# copy $BACKUP_DIR somewhere off the Pi (rclone/scp) on your own schedule.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$APP_DIR/.env.production"
BACKUP_DIR="$APP_DIR/backups"
KEEP_DAYS=14

mkdir -p "$BACKUP_DIR"

DSN="$(grep -E '^DATABASE_URL=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true)"
if [ -z "$DSN" ]; then
  echo "backup_db: no DATABASE_URL in $ENV_FILE — nothing to back up (legacy mode?)."
  exit 0
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/sonuslabs-$STAMP.sql.gz"

# -Fc would be smaller/faster to restore, but plain SQL + gzip is trivially
# inspectable and restores with psql anywhere. Fine at this scale.
pg_dump "$DSN" | gzip -9 > "$OUT"
echo "backup_db: wrote $OUT ($(du -h "$OUT" | cut -f1))"

# rotate
find "$BACKUP_DIR" -name 'sonuslabs-*.sql.gz' -mtime +"$KEEP_DAYS" -delete 2>/dev/null || true
echo "backup_db: kept last $KEEP_DAYS days ($(find "$BACKUP_DIR" -name 'sonuslabs-*.sql.gz' | wc -l | tr -d ' ') dumps)."

# Restore reminder (do NOT run automatically):
#   gunzip -c sonuslabs-YYYYMMDD-HHMMSS.sql.gz | psql "$DATABASE_URL"
