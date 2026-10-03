"""Identity store: tenant registry, credentials, lockout counters, recovery codes, single-use
links and sessions, with row-level security per tenant.

Revision ID: i0001
Revises:
Create Date: 2026-10-03

Frozen once applied anywhere; add a new revision instead.

- The application role ``tarn_auth`` has no superuser, no BYPASSRLS and owns nothing. Only it
  (and the owner) may connect to this database; ``tarn_app`` cannot.
- Per-tenant tables: ENABLE + FORCE RLS, ``college_id = tarn_current_college()`` from the
  per-transaction setting ``app.college_id``.
- ``tenants``: everyone (``tarn_auth``) may read, because sign-in looks the tenant up by
  Institution ID before a college is known; inserts and updates only for the set college.
- Nothing is ever deleted by the app role except recovery codes being replaced.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "i0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tarn_auth"
_CURRENT = "tarn_current_college()"

TENANT_TABLES = {
    "identities": """
        user_id uuid PRIMARY KEY,
        college_id uuid NOT NULL REFERENCES tenants (college_id),
        login_handle text NOT NULL CHECK (btrim(login_handle) <> ''),
        email_canonical text NOT NULL CHECK (email_canonical = lower(btrim(email_canonical))),
        status text NOT NULL CHECK (status IN ('PENDING', 'ACTIVE', 'DISABLED')),
        password_hash text,
        last_password_change timestamptz,
        force_reset boolean NOT NULL DEFAULT false,
        email_verified_at timestamptz,
        recovery_version integer NOT NULL DEFAULT 0 CHECK (recovery_version >= 0),
        encrypted_master_recovery_token text,
        other_recovery_factors jsonb NOT NULL DEFAULT '[]',
        created_at timestamptz NOT NULL,
        updated_at timestamptz NOT NULL,
        seq bigint GENERATED ALWAYS AS IDENTITY,
        UNIQUE (college_id, user_id),
        UNIQUE (college_id, email_canonical),
        CHECK (status <> 'ACTIVE' OR password_hash IS NOT NULL),
        CHECK ((password_hash IS NULL) = (last_password_change IS NULL)),
        CHECK ((recovery_version = 0) = (encrypted_master_recovery_token IS NULL)),
        CHECK (jsonb_typeof(other_recovery_factors) = 'array')
    """,
    # Apart from the credentials, so a failed sign-in is one atomic UPDATE of this row.
    "login_counters": """
        user_id uuid PRIMARY KEY,
        college_id uuid NOT NULL,
        failed_login_attempts integer NOT NULL DEFAULT 0 CHECK (failed_login_attempts >= 0),
        lockout_until timestamptz,
        FOREIGN KEY (college_id, user_id) REFERENCES identities (college_id, user_id)
    """,
    "recovery_codes": """
        college_id uuid NOT NULL,
        user_id uuid NOT NULL,
        ordinal integer NOT NULL CHECK (ordinal >= 0),
        code_hash text NOT NULL CHECK (code_hash LIKE '$argon2id$%'),
        created_at timestamptz NOT NULL,
        used_at timestamptz,
        PRIMARY KEY (user_id, ordinal),
        FOREIGN KEY (college_id, user_id) REFERENCES identities (college_id, user_id)
    """,
    "action_tokens": """
        token_hash text PRIMARY KEY CHECK (token_hash ~ '^[0-9a-f]{64}$'),
        college_id uuid NOT NULL,
        user_id uuid NOT NULL,
        purpose text NOT NULL CHECK (purpose IN ('reset', 'invite', 'verify_email')),
        created_at timestamptz NOT NULL,
        expires_at timestamptz NOT NULL,
        used_at timestamptz,
        CHECK (expires_at > created_at),
        FOREIGN KEY (college_id, user_id) REFERENCES identities (college_id, user_id)
    """,
    "auth_sessions": """
        id uuid PRIMARY KEY,
        college_id uuid NOT NULL,
        user_id uuid NOT NULL,
        refresh_hash text NOT NULL CHECK (refresh_hash ~ '^[0-9a-f]{64}$'),
        previous_refresh_hash text,
        remember boolean NOT NULL,
        created_at timestamptz NOT NULL,
        expires_at timestamptz NOT NULL,
        revoked_at timestamptz,
        FOREIGN KEY (college_id, user_id) REFERENCES identities (college_id, user_id)
    """,
}

GRANTS = {
    "tenants": "SELECT, INSERT, UPDATE",
    "identities": "SELECT, INSERT, UPDATE",
    "login_counters": "SELECT, INSERT, UPDATE",
    "recovery_codes": "SELECT, INSERT, UPDATE, DELETE",
    "action_tokens": "SELECT, INSERT, UPDATE",
    "auth_sessions": "SELECT, INSERT, UPDATE",
}


def upgrade() -> None:
    statements = [
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} NOLOGIN;
            END IF;
        END $$
        """,
        f"ALTER ROLE {APP_ROLE} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT",
        # Only the owner and tarn_auth may connect to the identity database.
        f"""
        DO $$
        BEGIN
            EXECUTE format('REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC',
                           current_database());
            EXECUTE format('GRANT CONNECT ON DATABASE %I TO {APP_ROLE}', current_database());
        END $$
        """,
        "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
        f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
        """
        CREATE FUNCTION tarn_current_college() RETURNS uuid
        LANGUAGE sql STABLE AS
        $$ SELECT NULLIF(current_setting('app.college_id', true), '')::uuid $$
        """,
        r"""
        CREATE TABLE tenants (
            college_id uuid PRIMARY KEY,
            institution_id text NOT NULL UNIQUE
                CHECK (institution_id ~ '^[A-Z0-9_*&-]{1,20}$'),
            name text NOT NULL CHECK (btrim(name) <> ''),
            kms_key_ref text NOT NULL CHECK (btrim(kms_key_ref) <> ''),
            wrapped_data_key bytea NOT NULL,
            data_key_version integer NOT NULL CHECK (data_key_version >= 1),
            min_password_length integer NOT NULL CHECK (min_password_length BETWEEN 8 AND 128),
            argon_time_cost integer NOT NULL CHECK (argon_time_cost >= 1),
            argon_memory_cost integer NOT NULL CHECK (argon_memory_cost >= 1024),
            argon_parallelism integer NOT NULL CHECK (argon_parallelism >= 1),
            max_failed_logins integer NOT NULL CHECK (max_failed_logins >= 1),
            lockout_minutes integer NOT NULL CHECK (lockout_minutes >= 1),
            status text NOT NULL CHECK (status IN
                ('PENDING_VERIFICATION', 'PENDING_APPROVAL', 'ACTIVE', 'SUSPENDED')),
            approval_required boolean NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            email_verified_at timestamptz,
            approved_at timestamptz,
            approved_by text,
            seq bigint GENERATED ALWAYS AS IDENTITY
        )
        """,
        *(f"CREATE TABLE {name} ({columns})" for name, columns in TENANT_TABLES.items()),
        "CREATE INDEX ON action_tokens (college_id, user_id, purpose)",
        "CREATE INDEX ON auth_sessions (college_id, user_id)",
        # tenants: readable by all (sign-in), writable only for the set college.
        "ALTER TABLE tenants ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE tenants FORCE ROW LEVEL SECURITY",
        "CREATE POLICY tenant_read ON tenants FOR SELECT USING (true)",
        f"CREATE POLICY tenant_insert ON tenants FOR INSERT WITH CHECK (college_id = {_CURRENT})",
        f"CREATE POLICY tenant_update ON tenants FOR UPDATE USING (college_id = {_CURRENT}) "
        f"WITH CHECK (college_id = {_CURRENT})",
    ]
    for table in TENANT_TABLES:
        statements += [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"CREATE POLICY college_isolation ON {table} "
            f"USING (college_id = {_CURRENT}) WITH CHECK (college_id = {_CURRENT})",
        ]
    statements += [f"GRANT {grants} ON {table} TO {APP_ROLE}" for table, grants in GRANTS.items()]
    statements.append(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    for table in [*reversed(TENANT_TABLES), "tenants"]:
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION tarn_current_college()")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}")
    op.execute(
        f"""
        DO $$
        BEGIN
            EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM {APP_ROLE}', current_database());
            EXECUTE format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO PUBLIC',
                           current_database());
        END $$
        """
    )
