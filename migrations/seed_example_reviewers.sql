-- ============================================================================
-- scripts/seed_example_reviewers.sql — inserts example reviewers, for local
-- testing of scripts/review_question.py.
--
-- There is currently no signup/auth flow anywhere in this codebase —
-- `reviewers` is explicitly left "untouched... reused as-is" by every
-- migration (see migrations/README.md). Until a real reviewer-management
-- flow exists, reviewer rows are inserted manually, same as this.
--
-- college_id is left NULL on purpose: NULL = platform-level reviewer,
-- eligible to review the shared question bank (question_reviews)
-- regardless of college — exactly what's needed here, since `questions`
-- itself is a shared/global table with no tenancy (see migration 003's
-- closing comment). A non-NULL college_id would scope a reviewer to one
-- college's answer_reviews instead, which isn't what this is for.
--
-- Idempotent: fixed UUIDs, ON CONFLICT DO UPDATE, safe to re-run.
--
-- Usage:
--   psql -h $PGHOST -U $PGUSER -d $PGDATABASE -f scripts/seed_example_reviewers.sql
-- ============================================================================

BEGIN;

INSERT INTO reviewers (reviewer_id, name, role, email, college_id)
VALUES (
    '44444444-4444-4444-4444-444444444444',
    'Dr. Rao', 'teacher', 'rao@example.edu', NULL
)
ON CONFLICT (reviewer_id) DO UPDATE
    SET name = EXCLUDED.name,
        role = EXCLUDED.role,
        email = EXCLUDED.email;

INSERT INTO reviewers (reviewer_id, name, role, email, college_id)
VALUES (
    '55555555-5555-5555-5555-555555555555',
    'Prof. Iyer', 'sme', 'iyer@example.edu', NULL
)
ON CONFLICT (reviewer_id) DO UPDATE
    SET name = EXCLUDED.name,
        role = EXCLUDED.role,
        email = EXCLUDED.email;

COMMIT;

-- reviewer_id values for use with review_question.py --reviewer-id:
--   Dr. Rao (teacher)    44444444-4444-4444-4444-444444444444
--   Prof. Iyer (sme)     55555555-5555-5555-5555-555555555555
