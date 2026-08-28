-- scripts/reset_db.sql — empties every data table (schema/migrations stay
-- intact) so scripts/seed_minimal.sql can insert a clean, minimal fixture
-- set. One TRUNCATE ... CASCADE covers all FK dependencies regardless of
-- table order, since every dependent table is listed here together.
--
-- Run via scripts/reset_and_seed_db.sh, not directly — that wrapper backs
-- up the DB first and sources connection details from .env.

SET app.is_platform_admin = 'true';

TRUNCATE TABLE
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
