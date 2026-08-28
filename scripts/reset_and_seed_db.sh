#!/usr/bin/env bash
# scripts/reset_and_seed_db.sh — backs up the DB, wipes all data
# (scripts/reset_db.sql), then loads the minimal fixture set
# (scripts/seed_minimal.sql). Schema/migrations are untouched — only data.
#
# Usage:
#   ./scripts/reset_and_seed_db.sh          # asks for a typed confirmation first
#   ./scripts/reset_and_seed_db.sh --yes    # skips the confirmation prompt

set -euo pipefail
cd "$(dirname "$0")/.."

set -a
source .env
set +a

if [[ "${1:-}" != "--yes" ]]; then
  echo "This will DELETE ALL DATA in database '$PGDATABASE' on $PGHOST:$PGPORT"
  echo "and replace it with the minimal fixture set (scripts/seed_minimal.sql)."
  read -r -p "Type 'reset' to continue: " confirm
  if [[ "$confirm" != "reset" ]]; then
    echo "Aborted — no changes made."
    exit 1
  fi
fi

mkdir -p db_backups
BACKUP_FILE="db_backups/ai_evaluation_backup_$(date +%Y%m%d_%H%M%S).sql"
echo ">>> Backing up to $BACKUP_FILE ..."
PGPASSWORD="$PGPASSWORD" pg_dump -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -F c -f "$BACKUP_FILE"

echo ">>> Truncating all data tables ..."
PGPASSWORD="$PGPASSWORD" psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -v ON_ERROR_STOP=1 -f scripts/reset_db.sql

echo ">>> Loading minimal seed data ..."
PGPASSWORD="$PGPASSWORD" psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -v ON_ERROR_STOP=1 -f scripts/seed_minimal.sql

echo ">>> Done. Row counts:"
PGPASSWORD="$PGPASSWORD" psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -c "
SET app.is_platform_admin = 'true';
ANALYZE;
SELECT relname AS table, n_live_tup AS approx_rows
FROM pg_stat_user_tables
WHERE n_live_tup > 0
ORDER BY relname;
"
