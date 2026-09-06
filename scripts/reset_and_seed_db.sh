#!/usr/bin/env bash
# scripts/reset_and_seed_db.sh — backs up the DB, wipes all data
# (migrations/reset_db.sql), then loads the minimal fixture set
# (migrations/seed_minimal.sql). Schema/migrations are untouched — only data.
#
# Usage:
#   ./scripts/reset_and_seed_db.sh          # asks for a typed confirmation first
#   ./scripts/reset_and_seed_db.sh --yes    # skips the confirmation prompt

set -euo pipefail
cd "$(dirname "$0")/.."

set -a
source .env
set +a

# ── RUNS AS THE OWNER ROLE, NOT AS THE APPLICATION ROLE ─────────────────────
# PGUSER points at the non-superuser application role (migration 016), which
# by design cannot do either half of this script:
#
#   * TRUNCATE — not granted. TRUNCATE is NOT filtered by row-level security,
#     so one statement would empty every tenant at once; that is an operator
#     action, not something the application role should be able to do.
#   * pg_dump — would "succeed" and produce a backup with ZERO ROWS in all
#     nine RLS-protected tables (students, exams, answers, answer_blocks,
#     evaluation_results, answer_reviews, answer_status_history,
#     evaluation_jobs, booklet_uploads): no tenant context is set during a
#     dump, and RLS fails closed silently. A restorable-looking empty backup
#     is the worst possible outcome for this script.
#
# So everything below uses PGADMIN_USER (default: postgres, the role that owns
# the schema and runs the migrations). seed_minimal.sql sets
# app.is_platform_admin itself, which is what lets its INSERTs cross tenants.
ADMIN_USER="${PGADMIN_USER:-postgres}"
ADMIN_PASSWORD="${PGADMIN_PASSWORD:-}"
echo ">>> Connecting as '$ADMIN_USER' (owner role); the app's PGUSER is '${PGUSER:-unset}'."

if [[ "${1:-}" != "--yes" ]]; then
  echo "This will DELETE ALL DATA in database '$PGDATABASE' on $PGHOST:$PGPORT"
  echo "and replace it with the minimal fixture set (migrations/seed_minimal.sql)."
  echo
  echo "That INCLUDES EVERY LOGIN (users, refresh_tokens — migration 017)."
  echo "The seed does not create one: a password hash in a committed .sql file"
  echo "would be a credential shared by every checkout. Re-bootstrap after:"
  echo "  python scripts/bootstrap_platform_admin.py --email ... --name ..."
  read -r -p "Type 'reset' to continue: " confirm
  if [[ "$confirm" != "reset" ]]; then
    echo "Aborted — no changes made."
    exit 1
  fi
fi

mkdir -p db_backups
BACKUP_FILE="db_backups/ai_evaluation_backup_$(date +%Y%m%d_%H%M%S).sql"
echo ">>> Backing up to $BACKUP_FILE ..."
PGPASSWORD="$ADMIN_PASSWORD" pg_dump -h "$PGHOST" -p "$PGPORT" -U "$ADMIN_USER" -d "$PGDATABASE" -F c -f "$BACKUP_FILE"

echo ">>> Truncating all data tables ..."
PGPASSWORD="$ADMIN_PASSWORD" psql -h "$PGHOST" -p "$PGPORT" -U "$ADMIN_USER" -d "$PGDATABASE" -v ON_ERROR_STOP=1 -f migrations/reset_db.sql

echo ">>> Loading minimal seed data ..."
PGPASSWORD="$ADMIN_PASSWORD" psql -h "$PGHOST" -p "$PGPORT" -U "$ADMIN_USER" -d "$PGDATABASE" -v ON_ERROR_STOP=1 -f migrations/seed_minimal.sql

echo ">>> Every login was deleted. Re-create the platform admin with:"
echo "      python scripts/bootstrap_platform_admin.py --email ... --name ..."
echo ">>> Done. Row counts:"
PGPASSWORD="$ADMIN_PASSWORD" psql -h "$PGHOST" -p "$PGPORT" -U "$ADMIN_USER" -d "$PGDATABASE" -c "
SET app.is_platform_admin = 'true';
ANALYZE;
SELECT relname AS table, n_live_tup AS approx_rows
FROM pg_stat_user_tables
WHERE n_live_tup > 0
ORDER BY relname;
"
