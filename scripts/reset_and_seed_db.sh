#!/usr/bin/env bash
# scripts/reset_and_seed_db.sh — backs up the DB, wipes all data
# (migrations/reset_db.sql), then loads the minimal fixture set
# (migrations/seed_minimal.sql). Schema/migrations are untouched — only data.
#
# Usage:
#   ./scripts/reset_and_seed_db.sh                          # asks for a typed confirmation first
#   ./scripts/reset_and_seed_db.sh --yes                    # skips the confirmation prompt
#   ./scripts/reset_and_seed_db.sh --yes --force-external-host   # see the PGHOST guard below

set -euo pipefail
cd "$(dirname "$0")/.."

# .env is optional here on purpose: this script also runs inside the
# docker-compose `seed` service, where PG*/APP_DB_* arrive as real container
# environment variables (docker-compose.yml's `environment:` block) and no
# .env file exists in the image at all. A local checkout still gets its
# usual values from .env when one is present.
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

YES=false
FORCE_EXTERNAL_HOST=false
for arg in "$@"; do
  case "$arg" in
    --yes) YES=true ;;
    --force-external-host) FORCE_EXTERNAL_HOST=true ;;
  esac
done

# ── PGHOST GUARD ─────────────────────────────────────────────────────────
# This script TRUNCATES every data table. It exists to reset a disposable
# development/CI database, never a configured external one — but PGHOST is
# read from the environment like everything else here, so a stray .env
# pointed at a real staging/prod host would otherwise be nuked exactly like
# a throwaway container. Refuse unless PGHOST is a recognized local/disposable
# host, or the caller explicitly overrides with --force-external-host — the
# same kind of deliberate, spelled-out friction
# scripts/bootstrap_platform_admin.py already uses for --allow-additional.
_SAFE_HOSTS=(localhost 127.0.0.1 ::1 postgres db)
_host_is_safe=false
for h in "${_SAFE_HOSTS[@]}"; do
  if [[ "${PGHOST:-}" == "$h" ]]; then
    _host_is_safe=true
    break
  fi
done
if [[ "$_host_is_safe" != true && "$FORCE_EXTERNAL_HOST" != true ]]; then
  echo "REFUSING to run: PGHOST='${PGHOST:-unset}' is not a recognized" >&2
  echo "local/disposable database host (${_SAFE_HOSTS[*]})." >&2
  echo "This script TRUNCATES ALL DATA — it must never point at a real," >&2
  echo "configured external database." >&2
  echo "If PGHOST really is a disposable container under an unlisted name," >&2
  echo "rerun with --force-external-host as an explicit, deliberate override." >&2
  exit 1
fi

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

if [[ "$YES" != true ]]; then
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
