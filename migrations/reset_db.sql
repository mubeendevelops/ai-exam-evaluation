-- scripts/reset_db.sql — empties every data table (schema/migrations stay
-- intact) so scripts/seed_minimal.sql can insert a clean, minimal fixture
-- set. One TRUNCATE ... CASCADE covers all FK dependencies regardless of
-- table order, since every dependent table is listed here together.
--
-- Run via scripts/reset_and_seed_db.sh, not directly — that wrapper backs
-- up the DB first and sources connection details from .env.
--
-- THIS DESTROYS EVERY LOGIN, INCLUDING THE PLATFORM ADMIN. `users` and
-- `refresh_tokens` (migration 017) are listed explicitly below rather than
-- left to CASCADE from `colleges`/`reviewers` — they would be emptied either
-- way, and a table that disappears by cascade is one nobody remembers is
-- gone. After a reset, re-run:
--
--     python scripts/bootstrap_platform_admin.py --email ... --name ...
--
-- and re-create the college accounts. seed_minimal.sql deliberately does NOT
-- seed a login: a password hash in a committed .sql file is a credential
-- every checkout of this repo shares (the same objection migration 016 makes
-- about APP_DB_PASSWORD).

SET app.is_platform_admin = 'true';

TRUNCATE TABLE
    refresh_tokens,
    users,
    answer_reviews,
    answer_status_history,
    answer_blocks,
    answers,
    evaluation_results,
    question_asset_links,
    content_assets,
    paper_questions,
    paper_sections,
    generated_papers,
    pattern_slots,
    pattern_sections,
    paper_patterns,
    reference_answer_variants,
    question_reviews,
    question_status_history,
    topic_links,
    question_keywords,
    keywords,
    sentences,
    paragraphs,
    questions,
    glossary_terms,
    topics,
    exams,
    reviewers,
    students,
    colleges
    RESTART IDENTITY CASCADE;
