"""P27: unit transfers, the delegation inbox and SME removal (D163, D191-D195).

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-28

- ``users.removed_at``: the admin removed the SME; the row stays (units, reviews and the audit
  log still name the person), the account is inactive for good.
- ``unit_transfers``: history of the units moved between SMEs, insert only (the app role may
  SELECT and INSERT). Composite foreign keys to the unit, both owners, the admin and the
  request it answered, all of the same college.
- ``delegation_requests``: an SME asks the admin to move one of their units. States pending,
  done, declined, withdrawn; one pending request per unit (partial unique index); a closed
  request names who closed it and when; only a declined one has a note. SELECT, INSERT and
  UPDATE (no DELETE).

Both tables: college RLS, forced. The downgrade is for development: it refuses while a removed
user, a transfer or a request exists.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tarn_app"
_CURRENT = "tarn_current_college()"
_REASON = "CHECK ({0} = btrim({0}) AND length({0}) BETWEEN 1 AND 500)"


def _rls(table: str, grants: str) -> list[str]:
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY college_isolation ON {table} "
        f"USING (college_id = {_CURRENT}) WITH CHECK (college_id = {_CURRENT})",
        f"GRANT {grants} ON {table} TO {APP_ROLE}",
    ]


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN removed_at timestamptz")
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT users_removed_inactive "
        "CHECK (removed_at IS NULL OR NOT active)"
    )
    op.execute(
        f"""
        CREATE TABLE delegation_requests (
            college_id uuid NOT NULL REFERENCES colleges (id),
            id uuid NOT NULL,
            unit_id uuid NOT NULL,
            requested_by uuid NOT NULL,
            reason text NOT NULL {_REASON.format("reason")},
            requested_at timestamptz NOT NULL,
            state text NOT NULL DEFAULT 'pending'
                CHECK (state IN ('pending', 'done', 'declined', 'withdrawn')),
            closed_by uuid,
            closed_at timestamptz,
            note text NOT NULL DEFAULT '',
            seq bigint GENERATED ALWAYS AS IDENTITY,
            PRIMARY KEY (college_id, id),
            UNIQUE (id),
            CHECK ((state = 'pending') = (closed_by IS NULL AND closed_at IS NULL)),
            CHECK ((state = 'declined') = (note <> '')),
            CHECK (note = btrim(note) AND length(note) <= 500),
            FOREIGN KEY (college_id, unit_id) REFERENCES units (college_id, id),
            FOREIGN KEY (college_id, requested_by) REFERENCES users (college_id, id),
            FOREIGN KEY (college_id, closed_by) REFERENCES users (college_id, id)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE unit_transfers (
            college_id uuid NOT NULL REFERENCES colleges (id),
            id uuid NOT NULL,
            unit_id uuid NOT NULL,
            from_owner uuid NOT NULL,
            to_owner uuid NOT NULL CHECK (to_owner <> from_owner),
            reason text NOT NULL {_REASON.format("reason")},
            by_user uuid NOT NULL,
            at timestamptz NOT NULL,
            request_id uuid,
            seq bigint GENERATED ALWAYS AS IDENTITY,
            PRIMARY KEY (college_id, id),
            UNIQUE (id),
            FOREIGN KEY (college_id, unit_id) REFERENCES units (college_id, id),
            FOREIGN KEY (college_id, from_owner) REFERENCES users (college_id, id),
            FOREIGN KEY (college_id, to_owner) REFERENCES users (college_id, id),
            FOREIGN KEY (college_id, by_user) REFERENCES users (college_id, id),
            FOREIGN KEY (college_id, request_id)
                REFERENCES delegation_requests (college_id, id)
        )
        """
    )
    for statement in (
        "CREATE UNIQUE INDEX delegation_one_pending ON delegation_requests (college_id, unit_id) "
        "WHERE state = 'pending'",
        "CREATE INDEX ON delegation_requests (college_id, requested_by)",
        "CREATE INDEX ON unit_transfers (college_id, unit_id)",
        *_rls("delegation_requests", "SELECT, INSERT, UPDATE"),
        *_rls("unit_transfers", "SELECT, INSERT"),
        f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
    ):
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("users", "unit_transfers", "delegation_requests"):
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    used = bind.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM users WHERE removed_at IS NOT NULL) "
            "OR EXISTS (SELECT 1 FROM unit_transfers) "
            "OR EXISTS (SELECT 1 FROM delegation_requests)"
        )
    ).scalar()
    op.execute("ALTER TABLE users FORCE ROW LEVEL SECURITY")
    if used:
        raise RuntimeError(
            "removed SMEs, transfers or delegation requests exist: 0015 can only be "
            "downgraded without them"
        )
    for statement in (
        "DROP TABLE unit_transfers",
        "DROP TABLE delegation_requests",
        "ALTER TABLE users DROP CONSTRAINT users_removed_inactive",
        "ALTER TABLE users DROP COLUMN removed_at",
    ):
        op.execute(statement)
