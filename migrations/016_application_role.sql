-- =============================================================================
-- Migration 016 — the application role that makes RLS actually run
-- =============================================================================
--
-- WHAT THIS FIXES
--
-- Migrations 003, 014 and 015 put `tenant_isolation` / `platform_admin_bypass`
-- policies on nine tables and set FORCE ROW LEVEL SECURITY on all of them.
-- None of it has ever executed. Postgres exempts SUPERUSER and BYPASSRLS
-- roles from row-level security entirely, and FORCE closes the table-OWNER
-- loophole, not that one. With PGUSER=postgres — the .env.example default, so
-- every checkout — two different `app.current_college_id` values see the same
-- rows, and the only thing isolating tenants is the hand-written
-- `AND college_id = %s` predicate in core/jobs.py, core/uploads.py and
-- api/services/evaluation.py.
--
-- Those predicates stay (test_rls_isolation.py::
-- test_the_isolating_predicate_is_not_only_rls pins them). This migration adds
-- the second layer they were always meant to be belt-and-braces with: a role
-- the policies apply to.
--
-- WHAT IT CREATES
--
-- One login role, by default `ai_eval_app`:
--
--   NOSUPERUSER NOBYPASSRLS   — the whole point; RLS applies to it.
--   NOCREATEDB NOCREATEROLE NOREPLICATION
--   owns NOTHING              — every table stays owned by the migration
--                               runner. An owner could ALTER TABLE ... NO
--                               FORCE ROW LEVEL SECURITY, or DISABLE it, or
--                               DROP the policies. The application must not be
--                               able to turn off its own isolation.
--   DML grants only           — SELECT/INSERT/UPDATE/DELETE on the current
--                               tables plus default privileges for future
--                               ones. Deliberately NOT granted:
--                               * TRUNCATE — migrations/reset_db.sql is an
--                                 operator action, not an application one, and
--                                 TRUNCATE is not filtered by RLS: one
--                                 statement would empty every tenant at once.
--                               * CREATE on schema public, REFERENCES,
--                                 TRIGGER, and any ownership.
--
-- THE PASSWORD COMES FROM THE ENVIRONMENT, NOT FROM THIS FILE. This file is in
-- git; a literal here would be a committed credential, and re-running the
-- migration would silently reset the password back to it. Set APP_DB_PASSWORD
-- (and optionally APP_DB_USER) before running:
--
--     export APP_DB_PASSWORD='...'                 # or: set -a; source .env; set +a
--     psql -v ON_ERROR_STOP=1 -f migrations/016_application_role.sql
--
-- Then point PGUSER/PGPASSWORD at the new role (see .env.example). Roles are
-- cluster-wide, so on a shared cluster this needs running once; the grants
-- below are per-database and need running in each database that has this
-- schema.
--
-- AFTER THIS, TWO THINGS CHANGE BEHAVIOUR AND BOTH ARE INTENDED:
--   1. A tenant connection that forgets `SET LOCAL app.current_college_id`
--      now really does see zero rows (it always claimed to; now it does).
--   2. Anything that must cross tenants must set `app.is_platform_admin` —
--      the worker, the batch evaluators, core/booklet_persist.py, and the
--      test fixtures already do.
--
-- OPERATOR SCRIPTS STAY ON THE OWNER ROLE. scripts/reset_and_seed_db.sh runs
-- pg_dump/TRUNCATE/seed as PGADMIN_USER (default postgres), because a pg_dump
-- taken as this role would be SILENTLY EMPTY for all nine RLS-protected
-- tables — a backup that restores a database with no students in it.
--
-- Run AFTER: 001 through 015.
-- Idempotent: the role is created only if absent, and every grant is
--             re-issued (GRANT is itself idempotent). Wrapped in BEGIN/COMMIT
--             — role creation is transactional in Postgres, so a failure
--             leaves no half-made role.
-- =============================================================================

-- ── psql preamble: read the environment BEFORE opening the transaction, so a
--    missing password ends the script instead of aborting a transaction. ─────
\getenv app_db_user APP_DB_USER
\getenv app_db_password APP_DB_PASSWORD

\if :{?app_db_user}
\else
  \set app_db_user ai_eval_app
\endif

\if :{?app_db_password}
\else
  \echo '!! APP_DB_PASSWORD is not set in the environment.'
  \echo '!! This migration takes the application role password from the'
  \echo '!! environment on purpose — a literal in a committed .sql file is a'
  \echo '!! committed credential. Set it and re-run:'
  \echo '!!     export APP_DB_PASSWORD=...'
  \quit
\endif

BEGIN;

-- Transaction-local (is_local = true), so the password is gone at COMMIT and
-- never reaches a session variable another statement could read back. The
-- `IS NOT NULL` is not decoration: set_config RETURNS the value it set, and a
-- bare SELECT would print the password to the operator's terminal and into any
-- log the migration run is teed to.
SELECT set_config('mig016.app_role',     :'app_db_user',     true) IS NOT NULL AS role_loaded,
       set_config('mig016.app_password', :'app_db_password', true) IS NOT NULL AS password_loaded;

DO $$
DECLARE
    role_name TEXT := current_setting('mig016.app_role');
    password  TEXT := current_setting('mig016.app_password');
    db_name   TEXT := current_database();
BEGIN
    IF coalesce(role_name, '') = '' THEN
        RAISE EXCEPTION 'APP_DB_USER is set but empty — refusing to guess a role name';
    END IF;
    IF role_name = current_user THEN
        RAISE EXCEPTION
            'APP_DB_USER names the role running this migration (%). That role '
            'owns every table here, and the ALTER below would strip its '
            'superuser and BYPASSRLS attributes mid-migration. The application '
            'role must be a SEPARATE role that owns nothing.', role_name;
    END IF;
    IF coalesce(password, '') = '' THEN
        RAISE EXCEPTION
            'APP_DB_PASSWORD is set but empty. A passwordless application role '
            'is not a safe default; export a real value and re-run.';
    END IF;

    -- ── the role ────────────────────────────────────────────────────────────
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
        EXECUTE format('CREATE ROLE %I', role_name);
        RAISE NOTICE 'created role %', role_name;
    ELSE
        RAISE NOTICE 'role % already exists — reapplying attributes and grants',
                     role_name;
    END IF;

    -- Attributes are re-asserted on every run, not only at creation: an
    -- existing role that someone made a superuser to "fix" a permissions
    -- problem is exactly the state this migration exists to correct.
    EXECUTE format(
        'ALTER ROLE %I WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB '
        'NOCREATEROLE NOREPLICATION INHERIT PASSWORD %L',
        role_name, password);

    -- ── privileges: connect, read the schema, DML on the data ───────────────
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', db_name, role_name);
    EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', role_name);
    EXECUTE format(
        'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I',
        role_name);
    -- No sequences exist today (every PK is a uuid), but a future SERIAL
    -- column would otherwise fail at INSERT time with a permission error that
    -- points at the sequence rather than at this grant.
    EXECUTE format(
        'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I', role_name);

    -- Tables created LATER by the migration runner (i.e. migration 017+) get
    -- the same grants automatically. Without this, every new migration would
    -- have to remember, and the failure — the app cannot see the new table —
    -- looks like RLS failing closed.
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
        'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I', role_name);
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
        'GRANT USAGE, SELECT ON SEQUENCES TO %I', role_name);

    -- ── and the privileges this role must NOT have ──────────────────────────
    -- Revoked rather than merely not-granted, so a role that already carried
    -- them (created by hand, or by an earlier draft of this file) is corrected
    -- on re-run. TRUNCATE is the dangerous one: it ignores RLS.
    EXECUTE format('REVOKE TRUNCATE, REFERENCES, TRIGGER ON ALL TABLES IN SCHEMA public FROM %I',
                   role_name);
    EXECUTE format('REVOKE CREATE ON SCHEMA public FROM %I', role_name);
END $$;

-- ── assertions: this migration is worthless if any of these is false ────────
DO $$
DECLARE
    role_name TEXT := current_setting('mig016.app_role');
    owned     INT;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = role_name AND (rolsuper OR rolbypassrls)) THEN
        RAISE EXCEPTION
            'role % still bypasses RLS after this migration — every policy in '
            '003/014/015 would remain inert', role_name;
    END IF;

    SELECT count(*) INTO owned
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public'
       AND c.relowner = (SELECT oid FROM pg_roles WHERE rolname = role_name);
    IF owned > 0 THEN
        RAISE EXCEPTION
            'role % owns % object(s) in schema public. An owner can DISABLE or '
            'NO FORCE row level security on what it owns, i.e. switch off its '
            'own tenant isolation. Reassign them to the migration owner.',
            role_name, owned;
    END IF;

    -- Nine tables must be both RLS-enabled and FORCEd (003 x7, 014, 015).
    -- FORCE matters even here: the migration runner owns them, and any future
    -- maintenance connection as the owner would otherwise skip the policies.
    IF EXISTS (
        SELECT 1 FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public'
           AND c.relname IN ('students', 'exams', 'answers', 'answer_blocks',
                             'evaluation_results', 'answer_reviews',
                             'answer_status_history', 'evaluation_jobs',
                             'booklet_uploads')
           AND NOT (c.relrowsecurity AND c.relforcerowsecurity)
    ) THEN
        RAISE EXCEPTION
            'a tenant table is missing ENABLE/FORCE ROW LEVEL SECURITY — '
            'run migrations 003, 014 and 015 first';
    END IF;

    RAISE NOTICE
        'role % is NOSUPERUSER/NOBYPASSRLS, owns nothing, and has DML grants. '
        'Point PGUSER at it (see .env.example) — RLS is now load-bearing.',
        role_name;
END $$;

COMMIT;
