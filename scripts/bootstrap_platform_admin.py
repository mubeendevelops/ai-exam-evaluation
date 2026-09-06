#!/usr/bin/env python3
"""bootstrap_platform_admin.py — create the FIRST platform_admin account.

THE CHICKEN-AND-EGG THIS SOLVES

Every other account is created by an admin through the API. The first one
cannot be: there is nobody to authenticate as. So it is created here, by
someone with database credentials, which is the only authority that exists
before the first account does.

WHY THIS IS A SCRIPT AND NOT A ROW IN seed_minimal.sql

seed_minimal.sql is committed and is applied by scripts/reset_and_seed_db.sh
on every reset. A password hash in it would be a committed credential shared
by every checkout of this repo — the exact objection migration 016 raises
about putting APP_DB_PASSWORD in a .sql file. The password here comes from a
prompt or from BOOTSTRAP_ADMIN_PASSWORD, is never echoed, and never touches
disk.

WHAT IT CREATES, IN ONE TRANSACTION
  * a `reviewers` row (the shared actor identity, college_id NULL =
    platform-level, which is what migration 017's agreement trigger requires
    of a platform_admin), and
  * the `users` row that logs in as it (role='platform_admin',
    college_id NULL).

RLS: runs with `SET LOCAL app.is_platform_admin = 'true'`, the same convention
as evaluate_pending.py and the other system-level scripts (CLAUDE_CONTEXT.md
§6). It has to: a platform_admin's users row has college_id IS NULL, which
`tenant_isolation` can never match, so under a tenant context the INSERT would
be refused by WITH CHECK and the "does one already exist?" query would always
answer no.

REFUSES TO RUN TWICE. If any platform_admin already exists, this exits
non-zero without writing. Bootstrapping is a one-time operation; a second
"first" admin is either a mistake or someone quietly minting themselves
cross-tenant access, and neither should be a silent success. Use
--allow-additional to override deliberately.

USAGE
    export PGUSER=... PGPASSWORD=...          # see .env
    python scripts/bootstrap_platform_admin.py \
        --email admin@platform.example --name "Platform Admin" --dry-run
    # then re-run without --dry-run

    # non-interactive (CI / provisioning):
    BOOTSTRAP_ADMIN_PASSWORD='...' python scripts/bootstrap_platform_admin.py \
        --email admin@platform.example --name "Platform Admin"

Requires migration 017 (and 001-016) to have been applied.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

from core.db import transaction
from core.users import create_user, platform_admin_exists

#: Read once, named once. A password on the argv of a long-lived process is
#: visible in `ps` and in shell history — hence no --password flag at all.
PASSWORD_ENV = "BOOTSTRAP_ADMIN_PASSWORD"

MIN_PASSWORD_LENGTH = 12


def resolve_password(interactive: bool) -> str:
    """Environment first, then a double-entry prompt. Never argv."""
    from_env = os.environ.get(PASSWORD_ENV)
    if from_env:
        if len(from_env) < MIN_PASSWORD_LENGTH:
            raise SystemExit(
                f"{PASSWORD_ENV} is shorter than {MIN_PASSWORD_LENGTH} "
                f"characters. This account can read and write every college's "
                f"data; it does not get a weak password.")
        return from_env

    if not interactive:
        raise SystemExit(
            f"No {PASSWORD_ENV} in the environment and stdin is not a TTY, so "
            f"there is nothing to prompt. Set {PASSWORD_ENV} and re-run.")

    first = getpass.getpass("Password for the new platform_admin: ")
    if len(first) < MIN_PASSWORD_LENGTH:
        raise SystemExit(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if first != getpass.getpass("Repeat it: "):
        raise SystemExit("Passwords did not match; nothing was written.")
    return first


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create the first platform_admin account (migration 017).")
    parser.add_argument("--email", required=True,
                        help="Login email. Stored citext; case-insensitively unique.")
    parser.add_argument("--name", required=True,
                        help="Display name for the reviewers row.")
    parser.add_argument(
        "--reviewer-role", default="admin", choices=["teacher", "sme", "admin"],
        help="Value for reviewers.role (the reviewer_role ENUM from migration "
             "001 — a DIFFERENT vocabulary from users.role, which is fixed at "
             "platform_admin here). Default: admin.")
    parser.add_argument(
        "--allow-additional", action="store_true",
        help="Create the account even though a platform_admin already exists. "
             "Off by default; see this module's docstring.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Do everything, including the INSERTs and every constraint and "
             "trigger they fire, then ROLL BACK. The safe first run.")
    args = parser.parse_args()

    password = resolve_password(interactive=sys.stdin.isatty())

    try:
        with transaction(dry_run=args.dry_run) as cur:
            # Before anything else: platform_admin rows are invisible without
            # this, so the duplicate check below would be meaningless too.
            cur.execute("SET LOCAL app.is_platform_admin = 'true'")

            if platform_admin_exists(cur) and not args.allow_additional:
                print(
                    "A platform_admin account already exists. Refusing to "
                    "create another — bootstrapping is a one-time operation. "
                    "Pass --allow-additional if you really mean to.",
                    file=sys.stderr)
                return 2

            created = create_user(
                cur,
                name=args.name,
                email=args.email,
                password=password,
                role="platform_admin",
                college_id=None,               # required: see users_platform_admin_has_no_college
                reviewer_role=args.reviewer_role,
            )
    except psycopg2.errors.UniqueViolation:
        print(f"An account with email {args.email!r} already exists.",
              file=sys.stderr)
        return 2
    except psycopg2.errors.UndefinedTable:
        print("No `users` table — apply migrations/017_auth_identity.sql first.",
              file=sys.stderr)
        return 2
    except psycopg2.errors.InsufficientPrivilege as exc:
        print(f"Permission denied: {exc}\n"
              f"Migration 017 narrows the application role's grants on `users`. "
              f"Check PGUSER matches APP_DB_USER and that 017 ran.",
              file=sys.stderr)
        return 2

    verb = "WOULD create" if args.dry_run else "Created"
    print(f"{verb} platform_admin {args.email}")
    print(f"  user_id     = {created['user_id']}")
    print(f"  reviewer_id = {created['reviewer_id']}")
    if args.dry_run:
        print("\n--dry-run: the transaction was rolled back, nothing was written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
