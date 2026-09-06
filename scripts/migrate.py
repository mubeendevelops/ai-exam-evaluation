#!/usr/bin/env python3
"""scripts/migrate.py — schema_migrations tracking/runner.

Answers "is this DB current?" by querying the `schema_migrations` table
(migrations/018_schema_migrations.sql) instead of reading files and
guessing (CLAUDE_CONTEXT.md §10). Deliberately NOT a migration framework:
no model-diffing, no up/down pairs, no ORM awareness of the schema. These
migrations are hand-written SQL with triggers, RLS policies and partial
unique indexes (migrations/README.md) that a diffing tool would fight. This
script owns only sequencing and bookkeeping; the SQL stays hand-written.

Runs as the OWNER role, never as the application role: migrations
create/alter tables, roles and RLS policies, none of which the
NOSUPERUSER application role (migration 016) is granted.

    python scripts/migrate.py --status
    python scripts/migrate.py --up
    python scripts/migrate.py --baseline               # marks 001-015 applied
    python scripts/migrate.py --baseline --through 17   # a DB that already
                                                         # has 016/017 by hand

**Migration files are applied via `psql -f`, not psycopg2** — the same
invocation §8 has always documented, and not optional: 016 and 017 read
APP_DB_USER/APP_DB_PASSWORD through psql meta-commands (`\\getenv`, `\\if
:{?...}`) so that a password never has to appear as a literal in a
committed file, and psycopg2 sends raw SQL to the server with no client-side
preprocessing — it cannot interpret those directives at all, and silently
treating `\getenv` as SQL is a syntax error, not a fallback. Every migration
therefore goes through the same code path, whether or not it happens to need
that. Requires `psql` on PATH (the `postgresql-client` package). Connection
env is read the same way core.db.get_admin_connection() reads it
(PGADMIN_USER/PGADMIN_PASSWORD falling back to PGUSER/PGPASSWORD), and the
subprocess otherwise inherits this process's environment — which is what
lets 016's `\getenv` see APP_DB_USER/APP_DB_PASSWORD at all.

The schema_migrations bookkeeping (status reads, recording an applied
checksum) goes through core.db.get_admin_connection() directly — psycopg2 is
fine there, since none of that SQL is more than a handful of plain
statements.

Each pending migration is applied by its own `psql` invocation, immediately
followed by one INSERT into schema_migrations (its own small transaction).
One honest gap: every existing migration file (001-005, 008-017; not 006)
already wraps itself in its own BEGIN/COMMIT (§5 rule 5) — psql commits that
before this script's INSERT ever runs, so the two are not one atomic unit. A
crash in that narrow window leaves the DDL applied but unrecorded; on retry
a non-idempotent migration (bare CREATE ROLE, CREATE TYPE without a guard)
fails loudly rather than silently re-running, which is the safe failure
mode, but it does mean that window is not perfectly atomic. New migrations
should keep using guarded DDL (`IF NOT EXISTS`, etc.) for exactly this
reason.

Checksum drift (a file changed after this database recorded it as applied)
is refused, never "fixed" — see the module docstring in
migrations/018_schema_migrations.sql.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import core.db  # noqa: E402

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"
_FILENAME_RE = re.compile(r"^(\d{3})_.*\.sql$")

# Kept identical to migrations/018_schema_migrations.sql's CREATE TABLE.
# Applied (IF NOT EXISTS) before any status/apply logic runs, so this tool
# works even on a database that has never applied migration 018 itself —
# the tracking table has to exist before anything can be tracked, including
# its own migration file. There is no 007 (migrations/README.md,
# CLAUDE_CONTEXT.md §10); discovery below only lists files that actually
# exist on disk, so that gap is never something to error on.
_BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    checksum    TEXT NOT NULL
);
"""

# --baseline's default upper bound: the migrations that predate this tool
# and were applied by hand before any tracking existed.
DEFAULT_BASELINE_THROUGH = 15


def discover_migrations() -> list[tuple[str, pathlib.Path]]:
    """Every migrations/NNN_*.sql file, sorted by its numeric prefix."""
    found = []
    for path in MIGRATIONS_DIR.glob("*.sql"):
        m = _FILENAME_RE.match(path.name)
        if not m:
            continue
        found.append((m.group(1), path))
    found.sort(key=lambda pair: int(pair[0]))
    return found


def checksum_of(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ensure_tracking_table(cur) -> None:
    cur.execute(_BOOTSTRAP_SQL)


def _apply_via_psql(path: pathlib.Path) -> None:
    """Runs one migration file with `psql -f`, ON_ERROR_STOP=1.

    Raises subprocess.CalledProcessError (with stdout/stderr attached) on
    any failure, and FileNotFoundError if `psql` is not on PATH.
    """
    env = dict(os.environ)
    admin_user = os.environ.get("PGADMIN_USER") or os.environ.get("PGUSER", "postgres")
    admin_password = os.environ.get("PGADMIN_PASSWORD") or os.environ.get("PGPASSWORD", "")
    env["PGPASSWORD"] = admin_password
    cmd = [
        "psql",
        "-h", os.environ.get("PGHOST", "localhost"),
        "-p", os.environ.get("PGPORT", "5432"),
        "-U", admin_user,
        "-d", os.environ.get("PGDATABASE", "ai_evaluation"),
        "-v", "ON_ERROR_STOP=1",
        "-f", str(path),
    ]
    subprocess.run(cmd, env=env, check=True, capture_output=True, text=True)


def _applied_versions(cur) -> dict[str, str]:
    """version -> checksum, for every row already in schema_migrations."""
    cur.execute("SELECT version, checksum FROM schema_migrations")
    return {row[0]: row[1] for row in cur.fetchall()}


def cmd_status(conn) -> int:
    migrations = discover_migrations()
    with conn.cursor() as cur:
        _ensure_tracking_table(cur)
        applied = _applied_versions(cur)
    conn.commit()

    drift = False
    known = set()
    for version, path in migrations:
        known.add(version)
        current = checksum_of(path)
        if version not in applied:
            print(f"{version}  pending     {path.name}")
        elif applied[version] != current:
            print(f"{version}  DRIFT       {path.name}  (file changed since it was applied)")
            drift = True
        else:
            print(f"{version}  applied     {path.name}")

    for version in sorted(set(applied) - known):
        print(f"{version}  applied     (no matching file on disk)")

    if drift:
        print("\nDrift detected — see migrations/018_schema_migrations.sql's "
              "docstring. --up will refuse to run until this is resolved by "
              "hand.", file=sys.stderr)
    return 1 if drift else 0


def cmd_up(conn) -> int:
    migrations = discover_migrations()
    with conn.cursor() as cur:
        _ensure_tracking_table(cur)
        applied = _applied_versions(cur)
    conn.commit()

    applied_count = 0
    for version, path in migrations:
        current = checksum_of(path)
        if version in applied:
            if applied[version] != current:
                print(f"REFUSING to continue: {path.name} has changed since "
                      f"it was applied (checksum drift). Not re-running it "
                      f"and not overwriting the recorded checksum — resolve "
                      f"by hand.", file=sys.stderr)
                return 1
            continue

        print(f"Applying {path.name} ...")
        try:
            _apply_via_psql(path)
        except FileNotFoundError:
            print("`psql` not found on PATH — required to apply migration "
                  "files (016 and 017 use psql meta-commands, e.g. \\getenv, "
                  "that psycopg2 cannot interpret). Install the "
                  "postgresql-client package.", file=sys.stderr)
            return 1
        except subprocess.CalledProcessError as exc:
            print(f"FAILED applying {path.name}:\n{exc.stderr}", file=sys.stderr)
            print("Stopping here. Migrations already applied earlier in this "
                  "run are committed and stay applied.", file=sys.stderr)
            return 1

        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                (version, current),
            )
        conn.commit()
        print(f"  applied {version}")
        applied_count += 1

    if applied_count == 0:
        print("Up to date — nothing to apply.")
    else:
        print(f"Applied {applied_count} migration(s).")
    return 0


def cmd_baseline(conn, through: int) -> int:
    migrations = [(v, p) for v, p in discover_migrations() if int(v) <= through]
    with conn.cursor() as cur:
        _ensure_tracking_table(cur)
        applied = _applied_versions(cur)
    conn.commit()

    inserted = 0
    with conn.cursor() as cur:
        for version, path in migrations:
            if version in applied:
                continue
            cur.execute(
                "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                (version, checksum_of(path)),
            )
            inserted += 1
    conn.commit()
    print(f"Baselined {inserted} migration(s) through {through:03d} "
          f"(already-tracked versions left untouched, and not re-run).")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="schema_migrations tracker/runner — see this file's module docstring.")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true",
                        help="show applied/pending/drift for every migration file")
    group.add_argument("--up", action="store_true",
                        help="apply pending migrations in order, one transaction each, "
                             "stopping at the first failure")
    group.add_argument("--baseline", action="store_true",
                        help="mark pre-existing migrations as applied WITHOUT running them "
                             f"(default: through {DEFAULT_BASELINE_THROUGH:03d} — the "
                             "migrations that predate this tool; pass --through to raise it)")
    ap.add_argument("--through", type=int, default=DEFAULT_BASELINE_THROUGH,
                     help=f"--baseline only: highest version number to mark applied "
                          f"(default: {DEFAULT_BASELINE_THROUGH})")
    args = ap.parse_args(argv)

    conn = core.db.get_admin_connection()
    try:
        if args.status:
            return cmd_status(conn)
        if args.up:
            return cmd_up(conn)
        if args.baseline:
            return cmd_baseline(conn, args.through)
    finally:
        conn.close()
    return 0  # pragma: no cover — argparse's mutually-exclusive group makes this unreachable


if __name__ == "__main__":
    raise SystemExit(main())
