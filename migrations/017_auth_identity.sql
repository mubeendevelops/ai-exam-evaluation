-- =============================================================================
-- Migration 017 — authentication identity: users + refresh_tokens
-- =============================================================================
--
-- WHAT THIS ADDS, AND WHAT IT DELIBERATELY DOES NOT TOUCH
--
-- Two new tables. `reviewers` is NOT modified — not one column, not one
-- policy. That is the whole design decision, so it is recorded here rather
-- than in a commit message:
--
--   `reviewers` is the ACTOR: shared between the answer and question schemas,
--   referenced by six FKs (answer_reviews.reviewer_id,
--   answer_status_history.changed_by, question_reviews.reviewer_id,
--   question_status_history.changed_by, paper_patterns.created_by,
--   generated_papers.generated_by), nullable college_id, no RLS.
--
--   `users` is the CREDENTIAL + TENANCY for those actors who can log in.
--   One row per login, `reviewer_id` UNIQUE NOT NULL, so there is exactly one
--   identity per human and it is still the reviewer_id that appears in every
--   provenance row. Nothing about the existing schema changes meaning.
--
-- WHY NOT PUT THE AUTH COLUMNS ON `reviewers` (the rejected option)
--
--   1. `reviewers.college_id IS NULL` ALREADY MEANS SOMETHING ELSE. Migration
--      003 §1 defines it as "platform-level reviewer/SME, eligible on the
--      shared question bank." The auth model needs NULL to mean
--      "role = 'platform_admin'". Those are different populations:
--      seed_minimal.sql seeds a 'teacher' and an 'sme' with NULL college_id,
--      so the CHECK constraint below would not even apply to existing data.
--
--   2. IT WOULD SILENTLY DISARM THE CROSS-COLLEGE REVIEW TRIGGER.
--      fn_derive_and_check_answer_review_college() (003 §6, and its
--      answer_status_history twin at §7) does:
--
--          SELECT college_id INTO reviewer_college
--            FROM reviewers WHERE reviewer_id = NEW.reviewer_id;
--          IF reviewer_college IS NOT NULL AND reviewer_college <> parent_college
--             THEN RAISE EXCEPTION ...
--
--      NULL is the PERMISSIVE branch. A password_hash column obliges us to put
--      RLS on the table; the moment we do, that SELECT is policy-filtered, a
--      College-B reviewer's row is invisible from a College-A session,
--      reviewer_college comes back NULL, and the check PASSES. Cross-college
--      review isolation would switch itself off, in the direction that reads
--      as "platform-level reviewer, allowed", with nothing in the log — the
--      §6 fails-closed-looks-like-no-data failure mode, inside a security
--      trigger. Leaving RLS off instead means password hashes in a table with
--      no tenant isolation. There is no configuration of `reviewers` that
--      satisfies both constraints. That is what decided this.
--
--   3. THE ROLE VOCABULARIES DO NOT MERGE. reviewer_role is an ENUM
--      ('teacher','sme','admin'); the auth roles are
--      ('teacher','admin','platform_admin'). 'sme' has no login meaning,
--      'platform_admin' has no review meaning. One column cannot carry both
--      without becoming two columns.
--
--   4. reviewers.email is TEXT NOT NULL and NOT unique. Promoting it to the
--      citext UNIQUE login identifier is a data migration across a shared
--      table with six inbound FKs.
--
-- THE ONE COST, STATED PLAINLY: `reviewers.email` and `users.email` both
-- exist. users.email is the CREDENTIAL (citext, unique, what you log in with);
-- reviewers.email stays the display/contact field it has always been. They are
-- not synced, on purpose — syncing would make a login rename rewrite rows that
-- six FKs point at. See the COMMENT on users.email.
--
-- Run AFTER: 001 through 016. 016 in particular: this migration hands out
-- COLUMN-LEVEL privileges to the application role, which must already exist.
-- Idempotent: CREATE EXTENSION/TABLE/INDEX IF NOT EXISTS, guarded ADD
--             CONSTRAINT, DROP POLICY/TRIGGER IF EXISTS before CREATE,
--             CREATE OR REPLACE FUNCTION, and GRANT/REVOKE are themselves
--             idempotent. Wrapped in BEGIN/COMMIT (rule 5).
-- =============================================================================

-- ── psql preamble: which role gets the (deliberately narrowed) grants. Must
--    match APP_DB_USER / PGUSER, i.e. the role migration 016 created. Read
--    before the transaction opens, exactly as 016 does. ─────────────────────
\getenv app_db_user APP_DB_USER

\if :{?app_db_user}
\else
  \set app_db_user ai_eval_app
\endif

BEGIN;

SELECT set_config('mig017.app_role', :'app_db_user', true) IS NOT NULL AS role_loaded;

-- citext: email comparison must be case-insensitive at the UNIQUE INDEX, not
-- in application code. lower(email) + a functional unique index would work
-- too, but then every query site has to remember the lower(); one that forgets
-- gets a duplicate account rather than a "user exists" error.
CREATE EXTENSION IF NOT EXISTS citext;

-- =============================================================================
-- 1. users
-- =============================================================================
--
-- CARDINALITY / NULLABILITY (rule 4), column by column:
--
--   reviewer_id  NOT NULL, UNIQUE  — exactly ONE users row per reviewers row,
--                                    and a reviewers row may have NO user
--                                    (an SME who reviews but never logs in,
--                                     or a historical reviewer). So
--                                    reviewers 1 : 0..1 users.
--                                    ON DELETE RESTRICT: a reviewer with a
--                                    login is not deletable out from under it,
--                                    matching answers' ON DELETE RESTRICT.
--   email        NOT NULL, UNIQUE  — the login identifier.
--   password_hash NOT NULL         — a hash, never a password. The app role
--                                    cannot SELECT this column at all (see §4).
--   role         NOT NULL          — TEXT + CHECK rather than an ENUM, because
--                                    reviewer_role already exists with a
--                                    different member list and a second enum
--                                    named like it would be a permanent
--                                    confusion. Adding a role later is then an
--                                    ALTER of this CHECK, not ALTER TYPE.
--   college_id   NULL IFF platform_admin — biconditional, see the CHECK below.
--                                    colleges 1 : 0..N users.
--   is_active    NOT NULL DEFAULT true — deactivation is not deletion; the
--                                    reviewer_id must survive so past reviews
--                                    keep resolving.
CREATE TABLE IF NOT EXISTS users (
    user_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reviewer_id    UUID        NOT NULL UNIQUE
                               REFERENCES reviewers(reviewer_id) ON DELETE RESTRICT,
    email          CITEXT      NOT NULL UNIQUE,
    password_hash  TEXT        NOT NULL,
    role           TEXT        NOT NULL,
    college_id     UUID        NULL REFERENCES colleges(college_id),
    is_active      BOOLEAN     NOT NULL DEFAULT true,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$ BEGIN
    ALTER TABLE users ADD CONSTRAINT users_role_check
        CHECK (role IN ('teacher', 'admin', 'platform_admin'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- BICONDITIONAL, not a one-way implication. `college_id IS NULL OR role <>
-- 'platform_admin'` would still admit a platform_admin pinned to one college —
-- an account that RLS would scope to a single tenant while its role claims it
-- can cross them, which is the more dangerous of the two mistakes.
DO $$ BEGIN
    ALTER TABLE users ADD CONSTRAINT users_platform_admin_has_no_college
        CHECK ((role = 'platform_admin') = (college_id IS NULL));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE users ADD CONSTRAINT users_email_not_blank
        CHECK (btrim(email::text) <> '');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS idx_users_college_id ON users (college_id);
CREATE INDEX IF NOT EXISTS idx_users_role       ON users (role);

-- -----------------------------------------------------------------------------
-- 1a. users.college_id must agree with reviewers.college_id.
--
-- NOT decoration. Without it, a user in College A whose reviewers row has
-- college_id IS NULL is, to 003's trigger, a "platform-level reviewer" — and
-- can therefore write answer_reviews rows against COLLEGE B's answers. The
-- trigger's permissive NULL branch would be reachable from an authenticated
-- ordinary request. This is the guarantee the rejected option (a) could not
-- have offered at all, because there NULL had to stay permissive for real
-- platform reviewers.
--
-- The rule: both NULL (platform_admin ⇔ platform-level reviewer), or equal.
-- Enforced on users, where the pairing is created — reviewers is never
-- written by this migration and its own updates are covered by the second
-- trigger below.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_check_user_reviewer_college_agrees()
RETURNS TRIGGER AS $$
DECLARE
    reviewer_college UUID;
    reviewer_exists  BOOLEAN;
BEGIN
    SELECT true, r.college_id INTO reviewer_exists, reviewer_college
      FROM reviewers r WHERE r.reviewer_id = NEW.reviewer_id;

    IF NOT coalesce(reviewer_exists, false) THEN
        -- The FK would catch this too; this message says WHICH row and why it
        -- matters, and it fires before the FK on the UPDATE path.
        RAISE EXCEPTION
            'users.reviewer_id % does not exist in reviewers', NEW.reviewer_id;
    END IF;

    IF reviewer_college IS DISTINCT FROM NEW.college_id THEN
        RAISE EXCEPTION
            'user % (college %) is linked to reviewer % (college %). They must '
            'match, or both be NULL. A college-scoped user pointing at a '
            'NULL-college (platform-level) reviewer would pass '
            'fn_derive_and_check_answer_review_college()''s permissive NULL '
            'branch and could review ANOTHER college''s answers.',
            NEW.email, coalesce(NEW.college_id::text, 'NULL'),
            NEW.reviewer_id, coalesce(reviewer_college::text, 'NULL');
    END IF;

    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_users_reviewer_college_agrees ON users;
CREATE TRIGGER trg_users_reviewer_college_agrees
    BEFORE INSERT OR UPDATE ON users
    FOR EACH ROW
    EXECUTE FUNCTION fn_check_user_reviewer_college_agrees();

-- The other direction: moving a REVIEWER between colleges (or to/from
-- platform level) must not silently break the pairing above. Without this,
-- the invariant holds only at users-write time and decays afterwards.
--
-- LIMIT, stated rather than implied: this trigger reads `users`, which is
-- RLS-protected, under the invoking session's own context. From a tenant
-- session it therefore cannot see a platform_admin's user row (college_id
-- NULL) and will let such a reviewer be moved. That is acceptable only
-- because reviewers is not writable from a tenant endpoint — reviewer
-- administration is a platform-admin operation, where every users row is
-- visible. If a tenant-facing "edit reviewer" endpoint is ever added, this
-- function must become SECURITY DEFINER first.
CREATE OR REPLACE FUNCTION fn_check_reviewer_college_agrees_with_user()
RETURNS TRIGGER AS $$
DECLARE
    user_college UUID;
    user_email   CITEXT;
    has_user     BOOLEAN;
BEGIN
    IF NEW.college_id IS NOT DISTINCT FROM OLD.college_id THEN
        RETURN NEW;
    END IF;

    SELECT true, u.college_id, u.email INTO has_user, user_college, user_email
      FROM users u WHERE u.reviewer_id = NEW.reviewer_id;

    IF coalesce(has_user, false) AND user_college IS DISTINCT FROM NEW.college_id THEN
        RAISE EXCEPTION
            'reviewer % is moving to college %, but its login (%) is scoped to '
            'college %. Move the user row in the same transaction — see '
            'trg_users_reviewer_college_agrees.',
            NEW.reviewer_id, coalesce(NEW.college_id::text, 'NULL'),
            user_email, coalesce(user_college::text, 'NULL');
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_reviewers_college_agrees_with_user ON reviewers;
CREATE TRIGGER trg_reviewers_college_agrees_with_user
    BEFORE UPDATE OF college_id ON reviewers
    FOR EACH ROW
    EXECUTE FUNCTION fn_check_reviewer_college_agrees_with_user();

-- =============================================================================
-- 2. refresh_tokens — so logout genuinely invalidates
-- =============================================================================
--
-- A stateless JWT cannot be revoked before it expires; "logout" that only
-- drops the client's copy is a UI gesture, not a security control. The refresh
-- token is the long-lived half, so it is the half that gets a row.
--
-- CARDINALITY / NULLABILITY:
--   user_id     NOT NULL, ON DELETE CASCADE — users 1 : 0..N refresh_tokens.
--               CASCADE (not RESTRICT, unlike reviewer_id): a deleted user's
--               sessions have no meaning and must not outlive them.
--   token_hash  NOT NULL, UNIQUE — the SHA-256 of the token, NEVER the token.
--               A stolen database must not yield usable sessions. Also why
--               there is no `token` column to "temporarily" populate.
--   expires_at  NOT NULL — absolute expiry, independent of revocation.
--   revoked_at  NULL     — NULL = live. Set, not deleted: an audit of "when
--                          did this session end, and was it a logout or an
--                          expiry" is the reason to keep the row at all.
--   college_id  NULL     — DENORMALIZED from users, trigger-derived, never
--                          supplied by the caller. It exists solely so the RLS
--                          policy is the same one-column predicate as the other
--                          nine tenant tables instead of a subquery into users.
--                          NULL exactly for platform_admin sessions, which are
--                          therefore invisible to every tenant policy and
--                          reachable only under platform_admin_bypass.
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    token_hash   TEXT        NOT NULL UNIQUE,
    college_id   UUID        NULL REFERENCES colleges(college_id),
    issued_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    revoked_at   TIMESTAMPTZ NULL
);

DO $$ BEGIN
    ALTER TABLE refresh_tokens ADD CONSTRAINT refresh_tokens_expiry_after_issue
        CHECK (expires_at > issued_at);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE OR REPLACE FUNCTION fn_derive_refresh_token_college()
RETURNS TRIGGER AS $$
DECLARE
    owner_college UUID;
    owner_found   BOOLEAN;
BEGIN
    SELECT true, u.college_id INTO owner_found, owner_college
      FROM users u WHERE u.user_id = NEW.user_id;

    IF NOT coalesce(owner_found, false) THEN
        RAISE EXCEPTION 'refresh_tokens.user_id % does not exist in users',
                        NEW.user_id;
    END IF;

    -- Derived, never trusted from the caller — the same pattern as
    -- fn_derive_and_check_answer_college() in 003. A token whose college_id
    -- disagreed with its user's would be a session visible to the wrong tenant.
    NEW.college_id := owner_college;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_refresh_tokens_derive_college ON refresh_tokens;
CREATE TRIGGER trg_refresh_tokens_derive_college
    BEFORE INSERT OR UPDATE OF user_id, college_id ON refresh_tokens
    FOR EACH ROW
    EXECUTE FUNCTION fn_derive_refresh_token_college();

CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_id ON refresh_tokens (user_id);
-- "every live session for this user" — the logout-everywhere / password-change
-- query. Partial, because revoked and expired rows are kept forever for audit
-- and would otherwise dominate the index.
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_live
    ON refresh_tokens (user_id) WHERE revoked_at IS NULL;

-- =============================================================================
-- 3. Row-Level Security
-- =============================================================================
--
-- Same tenant_isolation + platform_admin_bypass pair as the nine tables in
-- 003/014/015, ENABLE + FORCE, so nothing here is a special case for anyone
-- reading §6 later.
--
-- Note what tenant_isolation does to platform_admin rows: their college_id is
-- NULL, `NULL = '<uuid>'::uuid` is NULL, and the policy does not match. A
-- tenant session cannot see, edit, or even confirm the existence of a
-- platform_admin account. That falls out of the CHECK in §1 rather than
-- needing its own rule.
--
-- THE LOGIN PROBLEM, AND WHY IT IS NOT SOLVED WITH A BYPASS
--
-- Authentication happens BEFORE any tenant context exists — resolving the
-- college is the OUTPUT of the login, not an input to it. The obvious answers
-- are both wrong:
--
--   * leave `users` unprotected — hashes readable from any session, and the
--     table would be the one hole in an otherwise uniform §6 story;
--   * set app.is_platform_admin for the login query — that flips the
--     permissive bypass policy on all NINE other tenant tables for the whole
--     transaction. An unauthenticated request would briefly hold cross-tenant
--     read on every answer in the platform. Absolutely not.
--
-- Instead the login read is a SINGLE-ROW WINDOW, opened two ways at once, and
-- neither alone is sufficient:
--
--   ROW ACCESS  — the `auth_lookup` policy below matches exactly one row: the
--                 one whose email equals `app.auth_lookup_email`. Not "all
--                 rows while a flag is set" — one row, named by the address
--                 being authenticated. Unset GUC ⇒ NULL ⇒ matches nothing.
--   COLUMN ACCESS — §4 REVOKEs table-wide SELECT from the application role and
--                 re-grants it column by column, WITHOUT password_hash. The
--                 hash is not readable by the application at all; it is only
--                 ever a return value of auth_lookup_user(), which is
--                 SECURITY DEFINER and owned by the migration owner.
--
-- So the application can read one row's non-secret columns per email it names,
-- and can obtain a hash only through a function that takes one email and
-- returns one row. There is no statement the application role can issue that
-- dumps the table, with or without the GUC set.
-- =============================================================================
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON users;
CREATE POLICY tenant_isolation ON users
    FOR ALL
    USING (college_id = current_setting('app.current_college_id', true)::uuid)
    WITH CHECK (college_id = current_setting('app.current_college_id', true)::uuid);

DROP POLICY IF EXISTS platform_admin_bypass ON users;
CREATE POLICY platform_admin_bypass ON users
    FOR ALL
    USING (current_setting('app.is_platform_admin', true) = 'true')
    WITH CHECK (current_setting('app.is_platform_admin', true) = 'true');

-- SELECT only: the login window can never write. nullif(...,'') so that an
-- explicitly-blank GUC behaves like an unset one instead of comparing against
-- the empty string.
DROP POLICY IF EXISTS auth_lookup ON users;
CREATE POLICY auth_lookup ON users
    FOR SELECT
    USING (email = nullif(current_setting('app.auth_lookup_email', true), '')::citext);

-- The same single-row window, named by USER ID instead of by email. Needed by
-- POST /auth/refresh, which starts from a refresh token: redeeming one yields
-- a user_id and nothing else, and the new access token has to carry the
-- account's CURRENT role, reviewer_id and college — re-read, not copied
-- forward from the old token, so that a demoted or deactivated account stops
-- being able to mint working access tokens the moment its row changes.
--
-- Not merged into `auth_lookup`: an OR of two GUCs in one policy would mean
-- either GUC opening the row, and the login path (which sets only the email
-- one) would then also be reachable by id. Two policies, each one row.
DROP POLICY IF EXISTS auth_session_lookup ON users;
CREATE POLICY auth_session_lookup ON users
    FOR SELECT
    USING (user_id = nullif(current_setting('app.auth_user_id', true), '')::uuid);

ALTER TABLE refresh_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE refresh_tokens FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON refresh_tokens;
CREATE POLICY tenant_isolation ON refresh_tokens
    FOR ALL
    USING (college_id = current_setting('app.current_college_id', true)::uuid)
    WITH CHECK (college_id = current_setting('app.current_college_id', true)::uuid);

DROP POLICY IF EXISTS platform_admin_bypass ON refresh_tokens;
CREATE POLICY platform_admin_bypass ON refresh_tokens
    FOR ALL
    USING (current_setting('app.is_platform_admin', true) = 'true')
    WITH CHECK (current_setting('app.is_platform_admin', true) = 'true');

-- Issuing a token at login, and redeeming/revoking one, are all pre-context
-- operations. Same single-row shape as `auth_lookup`: the window is one token
-- hash, named by the caller. On INSERT there is no hash to name yet, so the
-- issuing branch names the USER instead — one user's row, again not the table.
-- §4 removes the application role's direct privileges on this table entirely,
-- so these policies are only ever traversed from inside the SECURITY DEFINER
-- functions in §5.
DROP POLICY IF EXISTS auth_session_ops ON refresh_tokens;
CREATE POLICY auth_session_ops
    ON refresh_tokens
    FOR ALL
    USING (
        token_hash = nullif(current_setting('app.auth_token_hash', true), '')
        OR user_id  = nullif(current_setting('app.auth_issue_user_id', true), '')::uuid
    )
    WITH CHECK (
        token_hash = nullif(current_setting('app.auth_token_hash', true), '')
        OR user_id  = nullif(current_setting('app.auth_issue_user_id', true), '')::uuid
    );

-- The user_id branch is what auth_revoke_all_refresh_tokens() ("log out
-- everywhere", "the password just changed") rides on: one user's live tokens,
-- still not the table. Both GUCs unset ⇒ both sides NULL ⇒ no row matches, so
-- the window is closed by default rather than by remembering to close it.

-- =============================================================================
-- 4. Privileges: the application role must not be able to read a hash
-- =============================================================================
--
-- Migration 016 granted SELECT/INSERT/UPDATE/DELETE on ALL TABLES, and set
-- DEFAULT PRIVILEGES so that tables created by later migrations get the same.
-- That default is right for evaluation data and wrong for exactly one column
-- in this migration, so it is narrowed here rather than weakened there.
-- =============================================================================
DO $$
DECLARE
    role_name TEXT := current_setting('mig017.app_role');
BEGIN
    IF coalesce(role_name, '') = '' THEN
        RAISE EXCEPTION 'APP_DB_USER is set but empty — refusing to guess a role name';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
        RAISE EXCEPTION
            'application role % does not exist. Run '
            'migrations/016_application_role.sql first: this migration hands '
            'out COLUMN-LEVEL grants on users, and skipping them would leave '
            'password_hash readable by whatever role the app connects as.',
            role_name;
    END IF;

    -- ── users: everything except reading password_hash ──────────────────────
    -- Revoke the table-wide grant 016's DEFAULT PRIVILEGES just applied, then
    -- re-grant column by column. Column-level SELECT means `SELECT * FROM
    -- users` FAILS for the app role — loudly, with "permission denied for
    -- column password_hash" — which is the correct outcome and a far better
    -- error than a hash arriving somewhere it should not be.
    EXECUTE format('REVOKE ALL ON TABLE users FROM %I', role_name);
    EXECUTE format(
        'GRANT SELECT (user_id, reviewer_id, email, role, college_id, '
        'is_active, created_at, updated_at) ON users TO %I', role_name);
    -- INSERT and UPDATE on password_hash ARE granted: setting a password does
    -- not require reading one. Verification happens inside auth_lookup_user().
    EXECUTE format(
        'GRANT INSERT (user_id, reviewer_id, email, password_hash, role, '
        'college_id, is_active) ON users TO %I', role_name);
    EXECUTE format(
        'GRANT UPDATE (email, password_hash, role, college_id, is_active, '
        'updated_at) ON users TO %I', role_name);
    EXECUTE format('GRANT DELETE ON users TO %I', role_name);

    -- ── refresh_tokens: no direct access at all ─────────────────────────────
    -- Sessions are created, redeemed and revoked exclusively through the
    -- functions in §5. With no table privilege, the app role cannot exploit
    -- app.auth_token_hash / app.auth_issue_user_id even though it can set
    -- them — the RLS policy and the privilege system have to BOTH be
    -- satisfied, and only the definer functions satisfy the second.
    EXECUTE format('REVOKE ALL ON TABLE refresh_tokens FROM %I', role_name);

    RAISE NOTICE
        'role %: SELECT on users excludes password_hash; refresh_tokens is '
        'reachable only through the auth_* functions.', role_name;
END $$;

-- =============================================================================
-- 5. The pre-context auth entry points (SECURITY DEFINER, one row each)
-- =============================================================================
--
-- Every one of these sets its GUC transaction-locally (set_config(..., true)),
-- runs ONE statement against ONE row, and restores the GUC before returning,
-- so the window does not stay open for the rest of the caller's transaction.
--
-- SET search_path = pg_catalog, public on every function: a SECURITY DEFINER
-- function without a pinned search_path is the classic privilege-escalation
-- hole (a caller-controlled schema shadowing `users`).
-- =============================================================================

-- Deliberately does NOT filter on is_active, and does not distinguish "no such
-- email" from "wrong password" for its caller. The auth code must verify the
-- password hash before it looks at is_active, so that a disabled or
-- nonexistent account costs the same time as a live one — otherwise the login
-- endpoint is a user-enumeration oracle.
CREATE OR REPLACE FUNCTION auth_lookup_user(p_email CITEXT)
RETURNS TABLE (
    user_id       UUID,
    reviewer_id   UUID,
    email         CITEXT,
    password_hash TEXT,
    role          TEXT,
    college_id    UUID,
    is_active     BOOLEAN
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    previous TEXT := current_setting('app.auth_lookup_email', true);
BEGIN
    IF p_email IS NULL OR btrim(p_email::text) = '' THEN
        RETURN;
    END IF;

    PERFORM set_config('app.auth_lookup_email', p_email::text, true);

    RETURN QUERY
        SELECT u.user_id, u.reviewer_id, u.email, u.password_hash,
               u.role, u.college_id, u.is_active
          FROM public.users u
         WHERE u.email = p_email;

    PERFORM set_config('app.auth_lookup_email', coalesce(previous, ''), true);
END;
$$;

COMMENT ON FUNCTION auth_lookup_user(CITEXT) IS
    'The ONLY way to obtain a password_hash. One email in, at most one row '
    'out. SECURITY DEFINER because the login happens before any tenant '
    'context exists; NOT a bypass — it opens the users.auth_lookup policy for '
    'exactly the row whose email was passed, and closes it again. Does not '
    'filter on is_active on purpose: verify the hash first, then reject, or '
    'the endpoint becomes a user-enumeration oracle.';

-- Issue a session. college_id is derived by the trigger, not passed.
CREATE OR REPLACE FUNCTION auth_issue_refresh_token(
    p_user_id    UUID,
    p_token_hash TEXT,
    p_expires_at TIMESTAMPTZ
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    previous TEXT := current_setting('app.auth_issue_user_id', true);
    new_id   UUID;
BEGIN
    IF p_user_id IS NULL OR coalesce(btrim(p_token_hash), '') = '' THEN
        RAISE EXCEPTION 'auth_issue_refresh_token requires a user_id and a token hash';
    END IF;

    PERFORM set_config('app.auth_issue_user_id', p_user_id::text, true);

    INSERT INTO public.refresh_tokens (user_id, token_hash, expires_at)
    VALUES (p_user_id, p_token_hash, p_expires_at)
    RETURNING refresh_tokens.token_id INTO new_id;

    PERFORM set_config('app.auth_issue_user_id', coalesce(previous, ''), true);
    RETURN new_id;
END;
$$;

-- Redeem: returns the row only if it is genuinely live. The liveness test
-- lives HERE rather than in the caller's WHERE clause so that no call site can
-- forget `revoked_at IS NULL` — the one omission that would make logout
-- cosmetic, which is the entire reason this table exists.
CREATE OR REPLACE FUNCTION auth_redeem_refresh_token(p_token_hash TEXT)
RETURNS TABLE (
    token_id   UUID,
    user_id    UUID,
    college_id UUID,
    expires_at TIMESTAMPTZ
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    previous TEXT := current_setting('app.auth_token_hash', true);
BEGIN
    IF coalesce(btrim(p_token_hash), '') = '' THEN
        RETURN;
    END IF;

    PERFORM set_config('app.auth_token_hash', p_token_hash, true);

    RETURN QUERY
        SELECT t.token_id, t.user_id, t.college_id, t.expires_at
          FROM public.refresh_tokens t
         WHERE t.token_hash = p_token_hash
           AND t.revoked_at IS NULL
           AND t.expires_at > now();

    PERFORM set_config('app.auth_token_hash', coalesce(previous, ''), true);
END;
$$;

-- Logout. Returns true if this call is what revoked it (false = unknown hash,
-- or already revoked), so the caller can tell a real logout from a replay
-- without a second query.
CREATE OR REPLACE FUNCTION auth_revoke_refresh_token(p_token_hash TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    previous TEXT := current_setting('app.auth_token_hash', true);
    revoked  INT;
BEGIN
    IF coalesce(btrim(p_token_hash), '') = '' THEN
        RETURN false;
    END IF;

    PERFORM set_config('app.auth_token_hash', p_token_hash, true);

    UPDATE public.refresh_tokens
       SET revoked_at = now()
     WHERE token_hash = p_token_hash
       AND revoked_at IS NULL;
    GET DIAGNOSTICS revoked = ROW_COUNT;

    PERFORM set_config('app.auth_token_hash', coalesce(previous, ''), true);
    RETURN revoked > 0;
END;
$$;

-- Logout everywhere / after a password change. Takes a user id, not a hash, so
-- it does not fit the single-token window above; it runs under the definer's
-- own rights with an explicit user_id predicate instead. This is the one auth
-- function that touches more than one row, and it is bounded to one user.
CREATE OR REPLACE FUNCTION auth_revoke_all_refresh_tokens(p_user_id UUID)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    previous TEXT := current_setting('app.auth_issue_user_id', true);
    revoked  INT;
BEGIN
    IF p_user_id IS NULL THEN
        RETURN 0;
    END IF;

    -- Rides the auth_session_ops policy's user_id branch. The token-hash GUC
    -- is cleared first so a leftover single-token window cannot widen or
    -- narrow what this statement sees.
    PERFORM set_config('app.auth_token_hash', '', true);
    PERFORM set_config('app.auth_issue_user_id', p_user_id::text, true);

    UPDATE public.refresh_tokens
       SET revoked_at = now()
     WHERE user_id = p_user_id
       AND revoked_at IS NULL;
    GET DIAGNOSTICS revoked = ROW_COUNT;

    PERFORM set_config('app.auth_issue_user_id', coalesce(previous, ''), true);
    RETURN revoked;
END;
$$;

-- Re-read one account, by id, for POST /auth/refresh. Returns the same
-- non-secret columns as auth_lookup_user MINUS password_hash: refreshing a
-- session never needs a hash, and a function that returns one is a function
-- someone will later call from somewhere that should not have it.
--
-- WHY THE REFRESH PATH RE-READS THE ROW AT ALL, rather than copying the old
-- token's claims: role, college_id and is_active can change between the login
-- and the refresh. Copying claims forward would mean a deactivated user, or
-- one demoted out of a role, kept minting valid access tokens for as long as
-- they held a refresh token — which is exactly the window revocation exists
-- to close.
CREATE OR REPLACE FUNCTION auth_user_by_id(p_user_id UUID)
RETURNS TABLE (
    user_id     UUID,
    reviewer_id UUID,
    email       CITEXT,
    role        TEXT,
    college_id  UUID,
    is_active   BOOLEAN
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    previous TEXT := current_setting('app.auth_user_id', true);
BEGIN
    IF p_user_id IS NULL THEN
        RETURN;
    END IF;

    PERFORM set_config('app.auth_user_id', p_user_id::text, true);

    RETURN QUERY
        SELECT u.user_id, u.reviewer_id, u.email, u.role, u.college_id, u.is_active
          FROM public.users u
         WHERE u.user_id = p_user_id;

    PERFORM set_config('app.auth_user_id', coalesce(previous, ''), true);
END;
$$;

COMMENT ON FUNCTION auth_user_by_id(UUID) IS
    'One account, by id, without its password_hash — the read behind '
    'POST /auth/refresh. Opens the users.auth_session_lookup policy for '
    'exactly that row and closes it again.';

-- EXECUTE is granted to the application role only. Revoked from PUBLIC first:
-- Postgres grants EXECUTE on new functions to PUBLIC by default, which on a
-- SECURITY DEFINER function means every role in the cluster.
DO $$
DECLARE
    role_name TEXT := current_setting('mig017.app_role');
    fn        TEXT;
BEGIN
    FOREACH fn IN ARRAY ARRAY[
        'auth_lookup_user(citext)',
        'auth_user_by_id(uuid)',
        'auth_issue_refresh_token(uuid, text, timestamptz)',
        'auth_redeem_refresh_token(text)',
        'auth_revoke_refresh_token(text)',
        'auth_revoke_all_refresh_tokens(uuid)'
    ]
    LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', fn);
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I', fn, role_name);
    END LOOP;
END $$;

-- =============================================================================
-- 6. Comments
-- =============================================================================
COMMENT ON TABLE users IS
    'Login credentials + tenancy. The ACTOR identity stays in `reviewers` '
    '(shared with the question schema, six inbound FKs, no RLS); this table '
    'is 1:0..1 with it via reviewer_id, so a login never becomes a second '
    'identity for the same human. See the header of '
    'migrations/017_auth_identity.sql for why the auth columns are not on '
    '`reviewers` itself — chiefly that reviewers.college_id IS NULL already '
    'means "platform-level reviewer" and is the permissive branch of the '
    'cross-college trigger in 003.';
COMMENT ON COLUMN users.email IS
    'The login identifier. CITEXT so uniqueness is case-insensitive in the '
    'index rather than in application code. Distinct from reviewers.email, '
    'which is the display/contact address and is NOT unique — deliberately '
    'not synced, since renaming a login must not rewrite a row that six FKs '
    'point at.';
COMMENT ON COLUMN users.password_hash IS
    'A hash. The application role has INSERT/UPDATE but NOT SELECT on this '
    'column (migration 017 §4); the only read path is auth_lookup_user().';
COMMENT ON COLUMN users.college_id IS
    'The tenant this login acts within, and the value that becomes '
    'app.current_college_id. NULL IF AND ONLY IF role = ''platform_admin'' '
    '(users_platform_admin_has_no_college). Must equal the linked reviewer''s '
    'college_id (trg_users_reviewer_college_agrees) — otherwise a college-A '
    'user linked to a NULL-college reviewer could review college B''s answers '
    'through 003''s permissive NULL branch.';
COMMENT ON COLUMN users.role IS
    'One of teacher / admin / platform_admin. TEXT + CHECK, not an ENUM: '
    'reviewer_role already exists as an ENUM with a DIFFERENT member list '
    '(teacher/sme/admin) and two similarly-named enums would be a permanent '
    'source of confusion. Adding a role later is an ALTER of the CHECK.';
COMMENT ON TABLE refresh_tokens IS
    'One row per issued refresh token, so logout genuinely invalidates rather '
    'than merely dropping the client''s copy. Stores the token HASH, never the '
    'token. Rows are revoked (revoked_at), never deleted — "when and how did '
    'this session end" is the audit trail. The application role has NO direct '
    'privilege on this table; all access is via the auth_* functions.';
COMMENT ON COLUMN refresh_tokens.college_id IS
    'DENORMALIZED from users.college_id by trg_refresh_tokens_derive_college, '
    'never supplied by the caller — same pattern as answers.college_id in 003. '
    'Exists so the RLS policy is the same single-column predicate as the other '
    'tenant tables. NULL exactly for platform_admin sessions, which are '
    'therefore invisible to every tenant policy.';

-- =============================================================================
-- 7. Assertions — this migration is worthless if any of these is false
-- =============================================================================
DO $$
DECLARE
    role_name TEXT := current_setting('mig017.app_role');
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relname IN ('users', 'refresh_tokens')
           AND NOT (c.relrowsecurity AND c.relforcerowsecurity)
    ) THEN
        RAISE EXCEPTION 'users/refresh_tokens are missing ENABLE/FORCE ROW LEVEL SECURITY';
    END IF;

    -- The point of §4. has_column_privilege reports the EFFECTIVE privilege,
    -- so this catches a stray table-level GRANT re-added later just as well as
    -- a REVOKE that silently did nothing.
    IF has_column_privilege(role_name, 'public.users', 'password_hash', 'SELECT') THEN
        RAISE EXCEPTION
            'role % can SELECT users.password_hash. The column-level grants in '
            '§4 did not take — every hash in the database is readable by the '
            'application.', role_name;
    END IF;
    IF NOT has_column_privilege(role_name, 'public.users', 'email', 'SELECT') THEN
        RAISE EXCEPTION
            'role % cannot SELECT users.email — §4 revoked more than it '
            're-granted and the API cannot list its own users', role_name;
    END IF;
    IF has_table_privilege(role_name, 'public.refresh_tokens', 'SELECT') THEN
        RAISE EXCEPTION
            'role % has direct SELECT on refresh_tokens; sessions must be '
            'reachable only through the auth_* functions', role_name;
    END IF;

    -- auth_user_by_id is the refresh path's read and must never grow a
    -- password_hash column: it is called on a connection with no tenant
    -- context, from an endpoint that has not yet identified its caller.
    IF EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.proname = 'auth_user_by_id'
           AND 'password_hash' = ANY (p.proargnames)
    ) THEN
        RAISE EXCEPTION
            'auth_user_by_id() now returns password_hash. The only read path '
            'for a hash is auth_lookup_user(), which the login endpoint calls '
            'with an email the caller supplied; the refresh path must not be '
            'able to obtain one at all.';
    END IF;

    -- `reviewers` must be exactly as 003 left it. If a later edit ever puts
    -- RLS on it, 003's cross-college trigger starts reading NULL for other
    -- tenants' reviewers and silently permits cross-college review.
    IF EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relname = 'reviewers' AND c.relrowsecurity
    ) THEN
        RAISE EXCEPTION
            'reviewers now has ROW LEVEL SECURITY enabled. '
            'fn_derive_and_check_answer_review_college() reads reviewers under '
            'the invoker''s policies and treats an invisible row as NULL, i.e. '
            'as a platform-level reviewer allowed to review ANY college. That '
            'trigger''s guarantee is gone. This is the exact failure that made '
            'auth a separate table — do not re-introduce it.';
    END IF;

    RAISE NOTICE
        'users + refresh_tokens created, RLS forced, password_hash unreadable '
        'by %, reviewers untouched. Bootstrap the first platform_admin with '
        'scripts/bootstrap_platform_admin.py.', role_name;
END $$;

COMMIT;
