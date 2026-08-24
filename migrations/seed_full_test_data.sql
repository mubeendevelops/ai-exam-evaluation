-- ============================================================================
-- seed_full_test_data.sql — comprehensive test data for the entire system
-- ============================================================================
--
-- Seeds ALL tables so every script can be tested end-to-end:
--
--   generate_questions.py — needs active paragraphs            (reuses existing seeds)
--   review_question.py   — needs draft questions + reviewers   (Q6, Q7 draft; Q8 confirmed)
--   generate_paper.py    — needs a pattern + live questions     (Q1–Q5, Q9–Q18 live, long style)
--   evaluate_answer.py   — needs answers + reference variants   (A1–A3 pending)
--   evaluate_pending.py  — needs pending_evaluation answers     (A1–A3)
--   view_paper.py        — needs generated papers              (seeded by generate_paper.py run)
--   reword_question.py   — needs any existing question          (Q1–Q5 live)
--
-- Idempotent: fixed UUIDs, ON CONFLICT DO UPDATE/NOTHING throughout.
-- Re-run safely at any time.
--
-- IMPORTANT: This script sets app.is_platform_admin = 'true' to bypass RLS
-- on answer-schema tables. It also provides a college_id on answer rows, but
-- the trigger overwrites it from the student's college — this is correct
-- behaviour per the architecture.
--
-- Usage:
--   psql -h $PGHOST -U $PGUSER -d $PGDATABASE -f migrations/seed_full_test_data.sql
-- ============================================================================

BEGIN;

-- Bypass RLS for answer-schema table inserts
SET LOCAL app.is_platform_admin = 'true';

-- ============================================================================
-- 0. COLLEGE (tenant for answer-schema testing)
-- ============================================================================
INSERT INTO colleges (college_id, name, short_code, status)
VALUES ('11111111-1111-1111-1111-111111111111', 'Demo Engineering College', 'demo', 'active')
ON CONFLICT (college_id) DO UPDATE SET name = EXCLUDED.name;

-- ============================================================================
-- 1. REVIEWERS (extend existing platform-level with one college-scoped)
-- ============================================================================
-- Platform-level (from seed_example_reviewers.sql — included for one-file convenience)
INSERT INTO reviewers (reviewer_id, name, role, email, college_id)
VALUES
    ('44444444-4444-4444-4444-444444444444', 'Dr. Rao',    'teacher', 'rao@example.edu',   NULL),
    ('55555555-5555-5555-5555-555555555555', 'Prof. Iyer', 'sme',     'iyer@example.edu',  NULL),
    ('66666666-6666-6666-6666-666666666666', 'Dr. Kumar',  'teacher', 'kumar@demo.edu',
     '11111111-1111-1111-1111-111111111111')
ON CONFLICT (reviewer_id) DO UPDATE
    SET name = EXCLUDED.name, role = EXCLUDED.role, email = EXCLUDED.email;

-- ============================================================================
-- 2. PARAGRAPHS + TOPICS + TOPIC_LINKS (from seed_example_paragraphs.sql)
-- ============================================================================
INSERT INTO paragraphs (paragraph_id, content, source_document, status) VALUES
    ('7977786e-be58-52b9-8d6e-4a7aa0bb63c9',
     'Photosynthesis is the process by which green plants convert light energy into chemical energy stored in glucose. It occurs in the chloroplasts and requires carbon dioxide, water, and sunlight, producing oxygen as a byproduct.',
     'biology_notes_ch4.docx', 'active'),
    ('081cf05c-5f9a-5bd5-8159-669ae5e19833',
     'The TCP three-way handshake establishes a connection between a client and a server before any data is transferred. The client sends a SYN packet, the server responds with a SYN-ACK, and the client replies with an ACK, after which the connection is considered established.',
     'networking_unit2.docx', 'active'),
    ('38bce224-f028-5b6b-940b-2d09908f935b',
     'Database normalization is the process of organizing tables to reduce data redundancy and improve data integrity. First normal form (1NF) requires atomic column values, second normal form (2NF) removes partial dependencies on a composite key, and third normal form (3NF) removes transitive dependencies.',
     'dbms_unit3.docx', 'active'),
    ('5a1869f6-a7b4-5cd4-abb1-7f7f4437a417',
     'The French Revolution began in 1789, driven by widespread famine, high taxation, and resentment of the monarchy''s absolute power. It led to the abolition of the monarchy, the rise of Napoleon Bonaparte, and lasting changes to political structures across Europe.',
     'world_history_ch7.docx', 'active')
ON CONFLICT (paragraph_id) DO UPDATE
    SET content = EXCLUDED.content, source_document = EXCLUDED.source_document;

INSERT INTO topics (topic_id, name, parent_topic_id) VALUES
    ('a5f5c1b1-3b7a-5c3d-8a0e-1c2d3e4f5a6b', 'Photosynthesis',         NULL),
    ('c2d3e4f5-a6b7-5c8d-0e1f-2a3b4c5d6e7f', 'TCP/IP Networking',      NULL),
    ('e4f5a6b7-c8d9-5e0f-2a3b-4c5d6e7f8a9b', 'Database Normalization',  NULL),
    ('f6a7b8c9-d0e1-5f2a-4b5c-6d7e8f9a0b1c', 'Biology',                 NULL),  -- parent topic
    ('a7b8c9d0-e1f2-5a3b-5c6d-7e8f9a0b1c2d', 'World History',           NULL)
ON CONFLICT (topic_id) DO NOTHING;

INSERT INTO topic_links (link_id, entity_type, entity_id, topic_id) VALUES
    ('b1c2d3e4-f5a6-5b7c-9d0e-1f2a3b4c5d6e', 'paragraph', '7977786e-be58-52b9-8d6e-4a7aa0bb63c9', 'a5f5c1b1-3b7a-5c3d-8a0e-1c2d3e4f5a6b'),
    ('d3e4f5a6-b7c8-5d9e-1f2a-3b4c5d6e7f8a', 'paragraph', '081cf05c-5f9a-5bd5-8159-669ae5e19833', 'c2d3e4f5-a6b7-5c8d-0e1f-2a3b4c5d6e7f'),
    ('f5a6b7c8-d9e0-5f1a-3b4c-5d6e7f8a9b0c', 'paragraph', '38bce224-f028-5b6b-940b-2d09908f935b', 'e4f5a6b7-c8d9-5e0f-2a3b-4c5d6e7f8a9b')
ON CONFLICT (link_id) DO NOTHING;

-- ============================================================================
-- 3. SENTENCES (a few from the photosynthesis paragraph)
-- ============================================================================
INSERT INTO sentences (sentence_id, paragraph_id, content, sequence_order, is_question_worthy) VALUES
    ('aaa00001-0001-0001-0001-aaaaaaaaaaaa', '7977786e-be58-52b9-8d6e-4a7aa0bb63c9',
     'Photosynthesis is the process by which green plants convert light energy into chemical energy stored in glucose.', 1, TRUE),
    ('aaa00001-0002-0002-0002-aaaaaaaaaaaa', '7977786e-be58-52b9-8d6e-4a7aa0bb63c9',
     'It occurs in the chloroplasts and requires carbon dioxide, water, and sunlight, producing oxygen as a byproduct.', 2, TRUE)
ON CONFLICT (sentence_id) DO NOTHING;

-- ============================================================================
-- 4. KEYWORDS
-- ============================================================================
INSERT INTO keywords (keyword_id, term) VALUES
    ('bbb00001-0001-0001-0001-bbbbbbbbbbbb', 'photosynthesis'),
    ('bbb00002-0002-0002-0002-bbbbbbbbbbbb', 'TCP'),
    ('bbb00003-0003-0003-0003-bbbbbbbbbbbb', 'normalization'),
    ('bbb00004-0004-0004-0004-bbbbbbbbbbbb', 'chloroplast'),
    ('bbb00005-0005-0005-0005-bbbbbbbbbbbb', 'three-way handshake')
ON CONFLICT (keyword_id) DO NOTHING;

-- ============================================================================
-- 5. CONTENT_ASSETS (one diagram for testing asset links)
-- ============================================================================
INSERT INTO content_assets (asset_id, asset_type, blob_url, structured_data)
VALUES ('ccc00001-0001-0001-0001-cccccccccccc', 'diagram',
        'dummy-storage/diagrams/photosynthesis_process.png', NULL)
ON CONFLICT (asset_id) DO NOTHING;

-- ============================================================================
-- 6. QUESTIONS — mixed statuses for different testing needs
--
-- Live questions: Q1-Q5 (various styles) for answer/paper testing
--                 Q9-Q18 (all long, varying marks) for paper generation
-- Draft questions: Q6-Q7 for review_question.py --review testing
-- Confirmed question: Q8 for review_question.py --promote testing
-- ============================================================================

-- Q1-Q5: live questions — multiple styles for evaluate_answer.py testing
INSERT INTO questions (question_id, source_type, source_id, style, marks_max, status,
    supersedes_question_id, parent_question_id, question_group_id, content, is_ai_generated) VALUES
    ('aaaaaaaa-0001-0001-0001-aaaaaaaaaaaa', 'paragraph', '7977786e-be58-52b9-8d6e-4a7aa0bb63c9',
     'short', 5, 'live', NULL, NULL, '00aa00aa-0001-0001-0001-00aa00aa00aa',
     'What is the role of chloroplasts in photosynthesis?', FALSE),
    ('aaaaaaaa-0002-0002-0002-aaaaaaaaaaaa', 'paragraph', '081cf05c-5f9a-5bd5-8159-669ae5e19833',
     'long', 10, 'live', NULL, NULL, '00aa00aa-0002-0002-0002-00aa00aa00aa',
     'Explain the TCP three-way handshake process in detail, including the purpose of each step.', FALSE),
    ('aaaaaaaa-0003-0003-0003-aaaaaaaaaaaa', 'paragraph', '38bce224-f028-5b6b-940b-2d09908f935b',
     'short', 5, 'live', NULL, NULL, '00aa00aa-0003-0003-0003-00aa00aa00aa',
     'Differentiate between 1NF, 2NF, and 3NF with examples.', FALSE),
    ('aaaaaaaa-0004-0004-0004-aaaaaaaaaaaa', 'paragraph', '7977786e-be58-52b9-8d6e-4a7aa0bb63c9',
     'one_word', 1, 'live', NULL, NULL, '00aa00aa-0004-0004-0004-00aa00aa00aa',
     'Name the organelle where photosynthesis takes place.', FALSE),
    ('aaaaaaaa-0005-0005-0005-aaaaaaaaaaaa', 'paragraph', '081cf05c-5f9a-5bd5-8159-669ae5e19833',
     'mcq', 1, 'live', NULL, NULL, '00aa00aa-0005-0005-0005-00aa00aa00aa',
     'Which packet does the client send first in the TCP handshake? (a) ACK (b) SYN (c) FIN (d) RST', FALSE)
ON CONFLICT (question_id) DO UPDATE
    SET content = EXCLUDED.content, status = EXCLUDED.status;

-- Q6-Q7: draft questions — for review_question.py --review testing
INSERT INTO questions (question_id, source_type, source_id, style, marks_max, status,
    supersedes_question_id, parent_question_id, question_group_id, content, is_ai_generated) VALUES
    ('aaaaaaaa-0006-0006-0006-aaaaaaaaaaaa', 'paragraph', '7977786e-be58-52b9-8d6e-4a7aa0bb63c9',
     'short', 5, 'draft', NULL, NULL, '00aa00aa-0006-0006-0006-00aa00aa00aa',
     'What are the main inputs and outputs of the photosynthesis process?', TRUE),
    ('aaaaaaaa-0007-0007-0007-aaaaaaaaaaaa', 'paragraph', '38bce224-f028-5b6b-940b-2d09908f935b',
     'long', 10, 'draft', NULL, NULL, '00aa00aa-0007-0007-0007-00aa00aa00aa',
     'Explain the process of database normalization. Why is it important for data integrity?', TRUE)
ON CONFLICT (question_id) DO UPDATE
    SET content = EXCLUDED.content, status = EXCLUDED.status;

-- Q8: confirmed question — for review_question.py --promote testing
INSERT INTO questions (question_id, source_type, source_id, style, marks_max, status,
    supersedes_question_id, parent_question_id, question_group_id, content, is_ai_generated) VALUES
    ('aaaaaaaa-0008-0008-0008-aaaaaaaaaaaa', 'paragraph', '081cf05c-5f9a-5bd5-8159-669ae5e19833',
     'short', 3, 'confirmed', NULL, NULL, '00aa00aa-0008-0008-0008-00aa00aa00aa',
     'What does SYN-ACK signify in the TCP handshake?', TRUE)
ON CONFLICT (question_id) DO UPDATE
    SET content = EXCLUDED.content, status = EXCLUDED.status;

-- Q9-Q18: live long questions with varying marks for paper generation
-- The existing pattern needs: 10M, 3M, 7M, 6M, 4M, 5M (all long style)
-- We need enough unique questions to fill all leaf slots in the pattern.
INSERT INTO questions (question_id, source_type, source_id, style, marks_max, status,
    supersedes_question_id, parent_question_id, question_group_id, content, is_ai_generated) VALUES
    ('aaaaaaaa-0009-0009-0009-aaaaaaaaaaaa', 'paragraph', '7977786e-be58-52b9-8d6e-4a7aa0bb63c9',
     'long', 10, 'live', NULL, NULL, '00aa00aa-0009-0009-0009-00aa00aa00aa',
     'Describe the light-dependent and light-independent reactions of photosynthesis in detail.', FALSE),
    ('aaaaaaaa-000a-000a-000a-aaaaaaaaaaaa', 'paragraph', '081cf05c-5f9a-5bd5-8159-669ae5e19833',
     'long', 10, 'live', NULL, NULL, '00aa00aa-000a-000a-000a-00aa00aa00aa',
     'Compare and contrast TCP and UDP protocols, discussing reliability, ordering, and use cases.', FALSE),
    ('aaaaaaaa-000b-000b-000b-aaaaaaaaaaaa', 'paragraph', '38bce224-f028-5b6b-940b-2d09908f935b',
     'long', 10, 'live', NULL, NULL, '00aa00aa-000b-000b-000b-00aa00aa00aa',
     'Explain the concept of functional dependencies and their role in normalization up to BCNF.', FALSE),
    ('aaaaaaaa-000c-000c-000c-aaaaaaaaaaaa', 'paragraph', '5a1869f6-a7b4-5cd4-abb1-7f7f4437a417',
     'long', 10, 'live', NULL, NULL, '00aa00aa-000c-000c-000c-00aa00aa00aa',
     'Analyze the causes and consequences of the French Revolution on European political structures.', FALSE),
    ('aaaaaaaa-000d-000d-000d-aaaaaaaaaaaa', 'paragraph', '7977786e-be58-52b9-8d6e-4a7aa0bb63c9',
     'long', 10, 'live', NULL, NULL, '00aa00aa-000d-000d-000d-00aa00aa00aa',
     'Discuss the factors affecting the rate of photosynthesis and their practical implications.', FALSE),
    ('aaaaaaaa-000e-000e-000e-aaaaaaaaaaaa', 'paragraph', '081cf05c-5f9a-5bd5-8159-669ae5e19833',
     'long', 10, 'live', NULL, NULL, '00aa00aa-000e-000e-000e-00aa00aa00aa',
     'Explain how TCP ensures reliable data transfer including flow control and congestion avoidance.', FALSE),
    ('aaaaaaaa-000f-000f-000f-aaaaaaaaaaaa', 'paragraph', '38bce224-f028-5b6b-940b-2d09908f935b',
     'long', 3, 'live', NULL, NULL, '00aa00aa-000f-000f-000f-00aa00aa00aa',
     'Define first normal form (1NF) and give an example of a table violating it.', FALSE),
    ('aaaaaaaa-0010-0010-0010-aaaaaaaaaaaa', 'paragraph', '38bce224-f028-5b6b-940b-2d09908f935b',
     'long', 7, 'live', NULL, NULL, '00aa00aa-0010-0010-0010-00aa00aa00aa',
     'Explain second and third normal forms with examples of tables that violate each.', FALSE),
    ('aaaaaaaa-0011-0011-0011-aaaaaaaaaaaa', 'paragraph', '7977786e-be58-52b9-8d6e-4a7aa0bb63c9',
     'long', 6, 'live', NULL, NULL, '00aa00aa-0011-0011-0011-00aa00aa00aa',
     'Describe the role of chlorophyll in capturing light energy during photosynthesis.', FALSE),
    ('aaaaaaaa-0012-0012-0012-aaaaaaaaaaaa', 'paragraph', '081cf05c-5f9a-5bd5-8159-669ae5e19833',
     'long', 4, 'live', NULL, NULL, '00aa00aa-0012-0012-0012-00aa00aa00aa',
     'What is the purpose of the ACK packet in the TCP three-way handshake?', FALSE),
    ('aaaaaaaa-0013-0013-0013-aaaaaaaaaaaa', 'paragraph', '5a1869f6-a7b4-5cd4-abb1-7f7f4437a417',
     'long', 5, 'live', NULL, NULL, '00aa00aa-0013-0013-0013-00aa00aa00aa',
     'Discuss the role of the Estates-General in triggering the French Revolution.', FALSE),
    ('aaaaaaaa-0014-0014-0014-aaaaaaaaaaaa', 'paragraph', '38bce224-f028-5b6b-940b-2d09908f935b',
     'long', 5, 'live', NULL, NULL, '00aa00aa-0014-0014-0014-00aa00aa00aa',
     'What are the disadvantages of denormalization? When might it be justified?', FALSE)
ON CONFLICT (question_id) DO UPDATE
    SET content = EXCLUDED.content, status = EXCLUDED.status;

-- ============================================================================
-- 7. QUESTION_KEYWORDS (tag a few live questions)
-- ============================================================================
INSERT INTO question_keywords (question_id, keyword_id, weight) VALUES
    ('aaaaaaaa-0001-0001-0001-aaaaaaaaaaaa', 'bbb00001-0001-0001-0001-bbbbbbbbbbbb', 1.0),
    ('aaaaaaaa-0001-0001-0001-aaaaaaaaaaaa', 'bbb00004-0004-0004-0004-bbbbbbbbbbbb', 0.8),
    ('aaaaaaaa-0002-0002-0002-aaaaaaaaaaaa', 'bbb00002-0002-0002-0002-bbbbbbbbbbbb', 1.0),
    ('aaaaaaaa-0002-0002-0002-aaaaaaaaaaaa', 'bbb00005-0005-0005-0005-bbbbbbbbbbbb', 0.9),
    ('aaaaaaaa-0003-0003-0003-aaaaaaaaaaaa', 'bbb00003-0003-0003-0003-bbbbbbbbbbbb', 1.0)
ON CONFLICT (question_id, keyword_id) DO NOTHING;

-- ============================================================================
-- 8. QUESTION_ASSET_LINKS (link diagram to Q1)
-- ============================================================================
INSERT INTO question_asset_links (link_id, asset_id, role, question_id, reference_answer_variant_id)
VALUES ('ddd00001-0001-0001-0001-dddddddddddd', 'ccc00001-0001-0001-0001-cccccccccccc',
        'question_source', 'aaaaaaaa-0001-0001-0001-aaaaaaaaaaaa', NULL)
ON CONFLICT (link_id) DO NOTHING;

-- ============================================================================
-- 9. REFERENCE_ANSWER_VARIANTS (for live questions — needed by evaluate_answer.py)
-- ============================================================================
INSERT INTO reference_answer_variants (variant_id, question_id, variant_type, content, is_current) VALUES
    ('eee00001-0001-0001-0001-eeeeeeeeeeee', 'aaaaaaaa-0001-0001-0001-aaaaaaaaaaaa', 'short',
     'Chloroplasts are the organelles where photosynthesis takes place. They contain chlorophyll, which absorbs light energy and uses it to convert carbon dioxide and water into glucose and oxygen.', TRUE),
    ('eee00002-0002-0002-0002-eeeeeeeeeeee', 'aaaaaaaa-0002-0002-0002-aaaaaaaaaaaa', 'long',
     'The TCP three-way handshake consists of three steps: (1) The client sends a SYN packet to the server to initiate a connection. (2) The server responds with a SYN-ACK packet, acknowledging the client''s request and sending its own synchronization. (3) The client sends an ACK packet back to the server, confirming the connection is established. This process ensures both parties are ready to communicate and agree on initial sequence numbers for reliable data transfer.', TRUE),
    ('eee00003-0003-0003-0003-eeeeeeeeeeee', 'aaaaaaaa-0003-0003-0003-aaaaaaaaaaaa', 'short',
     '1NF requires atomic values in each column (no repeating groups). 2NF requires that all non-key attributes depend on the entire primary key (eliminates partial dependencies). 3NF requires that non-key attributes depend only on the primary key (eliminates transitive dependencies). Example: a table with student_id, course_id, student_name violates 2NF because student_name depends only on student_id.', TRUE),
    ('eee00004-0004-0004-0004-eeeeeeeeeeee', 'aaaaaaaa-0004-0004-0004-aaaaaaaaaaaa', 'short',
     'Chloroplast', TRUE),
    ('eee00005-0005-0005-0005-eeeeeeeeeeee', 'aaaaaaaa-0005-0005-0005-aaaaaaaaaaaa', 'short',
     '(b) SYN', TRUE)
ON CONFLICT (variant_id) DO UPDATE
    SET content = EXCLUDED.content, is_current = EXCLUDED.is_current;

-- ============================================================================
-- 10. QUESTION_REVIEWS + STATUS_HISTORY (for already-reviewed questions)
-- ============================================================================
-- Q8 was reviewed (confirmed) by Dr. Rao — needed so its confirmed status has an audit trail
INSERT INTO question_reviews (review_id, question_id, reviewer_id, action, comment, reviewed_at)
VALUES ('fff00001-0001-0001-0001-ffffffffffff',
        'aaaaaaaa-0008-0008-0008-aaaaaaaaaaaa', '44444444-4444-4444-4444-444444444444',
        'confirmed', 'Good question, clear and concise.', now() - INTERVAL '1 hour')
ON CONFLICT (review_id) DO NOTHING;

INSERT INTO question_status_history (history_id, question_id, old_status, new_status, changed_by, changed_at)
VALUES ('fff00002-0002-0002-0002-ffffffffffff',
        'aaaaaaaa-0008-0008-0008-aaaaaaaaaaaa', 'draft', 'confirmed',
        '44444444-4444-4444-4444-444444444444', now() - INTERVAL '1 hour')
ON CONFLICT (history_id) DO NOTHING;

-- ============================================================================
-- 11. STUDENTS (Demo College)
-- ============================================================================
INSERT INTO students (student_id, college_id, name, roll_number, email) VALUES
    ('cccccccc-0001-0001-0001-cccccccccccc', '11111111-1111-1111-1111-111111111111',
     'Alice Sharma', 'DEMO2024001', 'alice@demo.edu'),
    ('cccccccc-0002-0002-0002-cccccccccccc', '11111111-1111-1111-1111-111111111111',
     'Bob Patel', 'DEMO2024002', 'bob@demo.edu')
ON CONFLICT (student_id) DO UPDATE
    SET name = EXCLUDED.name, email = EXCLUDED.email;

-- ============================================================================
-- 12. EXAMS (Demo College)
-- ============================================================================
INSERT INTO exams (exam_id, college_id, name, conducted_at, status) VALUES
    ('dddddddd-0001-0001-0001-dddddddddddd', '11111111-1111-1111-1111-111111111111',
     'AIML CIA-2 Mid-Term Aug 2026', now() - INTERVAL '2 days', 'live')
ON CONFLICT (exam_id) DO UPDATE
    SET name = EXCLUDED.name, status = EXCLUDED.status;

-- ============================================================================
-- 13. ANSWERS (pending_evaluation — for evaluate_answer.py / evaluate_pending.py)
--     Trigger derives college_id from student.college_id automatically.
--     Trigger also checks question status = 'live'.
-- ============================================================================
INSERT INTO answers (answer_id, college_id, question_id, student_id, exam_id,
    source_scan_url, text_extracted, status) VALUES
    -- Alice answers Q1 (short, photosynthesis)
    ('eeeeeeee-0001-0001-0001-eeeeeeeeeeee', '11111111-1111-1111-1111-111111111111',
     'aaaaaaaa-0001-0001-0001-aaaaaaaaaaaa', 'cccccccc-0001-0001-0001-cccccccccccc',
     'dddddddd-0001-0001-0001-dddddddddddd',
     'dummy-storage/scans/alice_q1.jpg',
     'Chloroplasts are where photosynthesis happens. They have chlorophyll that captures sunlight to make food for the plant.',
     'pending_evaluation'),
    -- Alice answers Q2 (long, TCP handshake)
    ('eeeeeeee-0002-0002-0002-eeeeeeeeeeee', '11111111-1111-1111-1111-111111111111',
     'aaaaaaaa-0002-0002-0002-aaaaaaaaaaaa', 'cccccccc-0001-0001-0001-cccccccccccc',
     'dddddddd-0001-0001-0001-dddddddddddd',
     'dummy-storage/scans/alice_q2.jpg',
     'TCP three-way handshake works as follows: First the client sends SYN to the server. Then server sends back SYN-ACK. Finally client sends ACK and the connection is established. This ensures both sides are ready before data transfer begins.',
     'pending_evaluation'),
    -- Bob answers Q1 (short, photosynthesis — same question, different student)
    ('eeeeeeee-0003-0003-0003-eeeeeeeeeeee', '11111111-1111-1111-1111-111111111111',
     'aaaaaaaa-0001-0001-0001-aaaaaaaaaaaa', 'cccccccc-0002-0002-0002-cccccccccccc',
     'dddddddd-0001-0001-0001-dddddddddddd',
     'dummy-storage/scans/bob_q1.jpg',
     'Plants use chloroplasts for photosynthesis.',
     'pending_evaluation')
ON CONFLICT (answer_id) DO UPDATE
    SET text_extracted = EXCLUDED.text_extracted, status = EXCLUDED.status;

-- ============================================================================
-- 14. ANSWER_BLOCKS (segments from OCR — text blocks for the answers above)
-- ============================================================================
INSERT INTO answer_blocks (block_id, college_id, answer_id, block_type, content, sequence_order, confidence_score) VALUES
    ('abababab-0001-0001-0001-abababababab', '11111111-1111-1111-1111-111111111111',
     'eeeeeeee-0001-0001-0001-eeeeeeeeeeee', 'text',
     'Chloroplasts are where photosynthesis happens. They have chlorophyll that captures sunlight to make food for the plant.',
     1, 0.92),
    ('abababab-0002-0002-0002-abababababab', '11111111-1111-1111-1111-111111111111',
     'eeeeeeee-0002-0002-0002-eeeeeeeeeeee', 'text',
     'TCP three-way handshake works as follows: First the client sends SYN to the server.',
     1, 0.95),
    ('abababab-0003-0003-0003-abababababab', '11111111-1111-1111-1111-111111111111',
     'eeeeeeee-0002-0002-0002-eeeeeeeeeeee', 'text',
     'Then server sends back SYN-ACK. Finally client sends ACK and the connection is established.',
     2, 0.88),
    ('abababab-0004-0004-0004-abababababab', '11111111-1111-1111-1111-111111111111',
     'eeeeeeee-0003-0003-0003-eeeeeeeeeeee', 'text',
     'Plants use chloroplasts for photosynthesis.',
     1, 0.97),
    -- A low-confidence diagram block (for future answer-preview / Task 2e testing)
    ('abababab-0005-0005-0005-abababababab', '11111111-1111-1111-1111-111111111111',
     'eeeeeeee-0002-0002-0002-eeeeeeeeeeee', 'diagram',
     NULL, 3, 0.45)
ON CONFLICT (block_id) DO NOTHING;

-- Set blob_url for the diagram block (can't use DEFAULT, so update after)
UPDATE answer_blocks SET blob_url = 'dummy-storage/scans/alice_q2_diagram.png'
WHERE block_id = 'abababab-0005-0005-0005-abababababab' AND blob_url IS NULL;

-- ============================================================================
-- 15. TOPIC_LINKS for questions (so topic reporting works)
-- ============================================================================
INSERT INTO topic_links (link_id, entity_type, entity_id, topic_id) VALUES
    ('11110001-0001-0001-0001-111100011111', 'question', 'aaaaaaaa-0001-0001-0001-aaaaaaaaaaaa', 'a5f5c1b1-3b7a-5c3d-8a0e-1c2d3e4f5a6b'),
    ('11110002-0002-0002-0002-111100021111', 'question', 'aaaaaaaa-0002-0002-0002-aaaaaaaaaaaa', 'c2d3e4f5-a6b7-5c8d-0e1f-2a3b4c5d6e7f'),
    ('11110003-0003-0003-0003-111100031111', 'question', 'aaaaaaaa-0003-0003-0003-aaaaaaaaaaaa', 'e4f5a6b7-c8d9-5e0f-2a3b-4c5d6e7f8a9b')
ON CONFLICT (link_id) DO NOTHING;

COMMIT;

-- ============================================================================
-- Quick-reference IDs for testing
-- ============================================================================
--
-- COLLEGE:
--   Demo College        11111111-1111-1111-1111-111111111111
--
-- REVIEWERS:
--   Dr. Rao (teacher)   44444444-4444-4444-4444-444444444444    (platform)
--   Prof. Iyer (sme)    55555555-5555-5555-5555-555555555555    (platform)
--   Dr. Kumar (teacher) 66666666-6666-6666-6666-666666666666    (Demo College)
--
-- PARAGRAPHS (active):
--   photosynthesis       7977786e-be58-52b9-8d6e-4a7aa0bb63c9
--   tcp_handshake        081cf05c-5f9a-5bd5-8159-669ae5e19833
--   normalization        38bce224-f028-5b6b-940b-2d09908f935b
--   french_revolution    5a1869f6-a7b4-5cd4-abb1-7f7f4437a417
--
-- QUESTIONS:
--   Q1 (short,5M,live)      aaaaaaaa-0001-0001-0001-aaaaaaaaaaaa
--   Q2 (long,10M,live)      aaaaaaaa-0002-0002-0002-aaaaaaaaaaaa
--   Q3 (short,5M,live)      aaaaaaaa-0003-0003-0003-aaaaaaaaaaaa
--   Q4 (one_word,1M,live)   aaaaaaaa-0004-0004-0004-aaaaaaaaaaaa
--   Q5 (mcq,1M,live)        aaaaaaaa-0005-0005-0005-aaaaaaaaaaaa
--   Q6 (short,5M,draft)     aaaaaaaa-0006-0006-0006-aaaaaaaaaaaa
--   Q7 (long,10M,draft)     aaaaaaaa-0007-0007-0007-aaaaaaaaaaaa
--   Q8 (short,3M,confirmed) aaaaaaaa-0008-0008-0008-aaaaaaaaaaaa
--   Q9-Q18 (long,various,live) for paper generation
--
-- REFERENCE VARIANTS:
--   V1 (for Q1)  eee00001-0001-0001-0001-eeeeeeeeeeee
--   V2 (for Q2)  eee00002-0002-0002-0002-eeeeeeeeeeee
--   V3 (for Q3)  eee00003-0003-0003-0003-eeeeeeeeeeee
--   V4 (for Q4)  eee00004-0004-0004-0004-eeeeeeeeeeee
--   V5 (for Q5)  eee00005-0005-0005-0005-eeeeeeeeeeee
--
-- STUDENTS (Demo College):
--   Alice Sharma  cccccccc-0001-0001-0001-cccccccccccc
--   Bob Patel     cccccccc-0002-0002-0002-cccccccccccc
--
-- EXAM:
--   AIML CIA-2    dddddddd-0001-0001-0001-dddddddddddd
--
-- ANSWERS (pending_evaluation):
--   A1 (Alice→Q1)  eeeeeeee-0001-0001-0001-eeeeeeeeeeee
--   A2 (Alice→Q2)  eeeeeeee-0002-0002-0002-eeeeeeeeeeee
--   A3 (Bob→Q1)    eeeeeeee-0003-0003-0003-eeeeeeeeeeee
--
-- TEST COMMANDS:
--   # Generate questions from paragraph:
--   python3 scripts/generate_questions.py 7977786e-be58-52b9-8d6e-4a7aa0bb63c9 --stub-llm --dry-run
--
--   # List draft questions / review / promote:
--   python3 scripts/review_question.py --list
--   python3 scripts/review_question.py --show aaaaaaaa-0006-0006-0006-aaaaaaaaaaaa
--   python3 scripts/review_question.py --review aaaaaaaa-0006-0006-0006-aaaaaaaaaaaa --reviewer-id 44444444-4444-4444-4444-444444444444 --action confirm
--   python3 scripts/review_question.py --promote aaaaaaaa-0008-0008-0008-aaaaaaaaaaaa --reviewer-id 44444444-4444-4444-4444-444444444444
--
--   # Reword a live question:
--   python3 scripts/reword_question.py aaaaaaaa-0001-0001-0001-aaaaaaaaaaaa --stub-llm --dry-run
--
--   # Evaluate single answer (stub):
--   python3 scripts/evaluate_answer.py eeeeeeee-0001-0001-0001-eeeeeeeeeeee eee00001-0001-0001-0001-eeeeeeeeeeee --method embeddings --stub --dry-run
--
--   # Evaluate all pending answers (stub):
--   python3 scripts/evaluate_pending.py --method embeddings --stub --dry-run
--
--   # Load pattern then generate paper:
--   python3 scripts/load_paper_pattern.py patterns/aiml_22cs53_cia_pattern.json
--   python3 scripts/generate_paper.py <pattern_id> --name "Test Paper" --show --dry-run
