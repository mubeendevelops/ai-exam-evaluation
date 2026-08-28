-- scripts/seed_minimal.sql — the smallest fixture set that keeps every
-- currently-working script demoable after scripts/reset_db.sql empties the
-- DB: one college/exam/student pair, a couple of reviewers, the REAL
-- college's actual paper pattern (kept verbatim — it's a genuine DSCE
-- 22CS53 pattern, not test clutter), the diagram-evaluation fixture chain
-- exactly as verified working in scripts/run_diagram_eval_demo.sh, and one
-- simple text-answer fixture for scripts/evaluate_answer.py.
--
-- Deliberately NOT reseeded (left empty — these are exactly the kind of
-- accumulated test-run artifacts this reset was meant to clear out):
--   - paragraphs / sentences / keywords / question_keywords — working data
--     from testing scripts/extract_exam_bank.py; regenerate by re-running
--     that script against a real source paper if needed again.
--   - generated_papers / paper_sections / paper_questions — a generated
--     paper references specific question_ids; run
--     scripts/generate_paper.py fresh once you have enough live questions
--     in a topic to fill the pattern's 16 slots.
--   - evaluation_results / *_status_history / *_reviews — append-only
--     history logs; start empty by design, populated by actually running
--     the eval/review scripts below.
--
-- Run via scripts/reset_and_seed_db.sh, not directly.

SET app.is_platform_admin = 'true';

-- ── College / people ────────────────────────────────────────────────────
INSERT INTO colleges (college_id, name, short_code, status) VALUES
    ('11111111-1111-1111-1111-111111111111', 'Demo Engineering College', 'demo', 'active');

INSERT INTO students (student_id, name, roll_number, email, college_id) VALUES
    ('cccccccc-0001-0001-0001-cccccccccccc', 'Alice Sharma', 'DEMO2024001', 'alice@demo.edu', '11111111-1111-1111-1111-111111111111'),
    ('cccccccc-0002-0002-0002-cccccccccccc', 'Bob Patel',    'DEMO2024002', 'bob@demo.edu',   '11111111-1111-1111-1111-111111111111');

INSERT INTO reviewers (reviewer_id, name, role, email, college_id) VALUES
    ('44444444-4444-4444-4444-444444444444', 'Dr. Rao',    'teacher', 'rao@example.edu',  NULL),
    ('55555555-5555-5555-5555-555555555555', 'Prof. Iyer', 'sme',     'iyer@example.edu', NULL);

INSERT INTO exams (exam_id, name, conducted_at, status, college_id) VALUES
    ('dddddddd-0001-0001-0001-dddddddddddd', 'AIML CIA-2 Mid-Term Aug 2026',
     '2026-08-22 18:42:50+05:30', 'live', '11111111-1111-1111-1111-111111111111');

-- ── Topics (only the two actually exercised by the fixtures below) ─────
INSERT INTO topics (topic_id, name) VALUES
    ('a1a1a1a1-0001-0001-0001-a1a1a1a1a1a1', 'Computer Architecture'),
    ('a1a1a1a1-0002-0002-0002-a1a1a1a1a1a1', 'Data Structures');

-- ── Paper pattern (real DSCE 22CS53 CIA pattern — kept verbatim) ───────
INSERT INTO paper_patterns (pattern_id, name, description, total_marks, course_code, is_active) VALUES
    ('15b05a44-6b4d-54b1-be21-753c495d7314',
     'AIML 22CS53 — CIA Standard Pattern (DSCE)',
     '4-section CIA pattern used by DSCE for AIML 22CS53: Q1+Q2 mandatory, Q3/Q4, Q5/Q6, Q7/Q8 each either-or. Derived from CIA2 and CIA3 reference papers.',
     50, '22CS53', true);

INSERT INTO pattern_sections (section_id, pattern_id, section_label, section_order, is_mandatory) VALUES
    ('b12ee7bf-57e1-5db7-9563-0a9d612d403c', '15b05a44-6b4d-54b1-be21-753c495d7314', 'Part A — Compulsory', 1, true),
    ('1ccd4195-4b2c-5e0f-a8e6-7b55df189a88', '15b05a44-6b4d-54b1-be21-753c495d7314', 'Part B — Either/Or',  2, false),
    ('9e53c22d-fa32-5c2d-8ada-d7770f93cbc6', '15b05a44-6b4d-54b1-be21-753c495d7314', 'Part C — Either/Or',  3, false),
    ('37f65f48-1e99-5705-b97d-ab49fedc7419', '15b05a44-6b4d-54b1-be21-753c495d7314', 'Part D — Either/Or',  4, false);

-- pattern_slots — the real DSCE pattern's slot structure, including
-- either-or sub-slots (Q2a/Q2b under Q2, etc. via parent_slot_id), kept
-- verbatim from the original data.
INSERT INTO pattern_slots (slot_id, section_id, slot_label, slot_order, marks, style, parent_slot_id) VALUES
    ('f79d44d6-bb34-5070-8d90-fc6d2aecdee7', 'b12ee7bf-57e1-5db7-9563-0a9d612d403c', 'Q1',  1, 10, 'long', NULL),
    ('48102913-baff-5404-9498-85301bd913e7', 'b12ee7bf-57e1-5db7-9563-0a9d612d403c', 'Q2',  2, 10, 'long', NULL),
    ('d7cc4040-0c8e-5ea0-b5e5-b7c72713c8bc', 'b12ee7bf-57e1-5db7-9563-0a9d612d403c', 'Q2a', 1, 3,  'long', '48102913-baff-5404-9498-85301bd913e7'),
    ('b1455e76-dde4-51f9-a3e9-24ce0d9f3ed0', 'b12ee7bf-57e1-5db7-9563-0a9d612d403c', 'Q2b', 2, 7,  'long', '48102913-baff-5404-9498-85301bd913e7'),
    ('a73d7448-7f0d-50d7-9852-ba97c42dfda3', '1ccd4195-4b2c-5e0f-a8e6-7b55df189a88', 'Q3',  1, 10, 'long', NULL),
    ('61f4a714-fc31-5529-bb3b-836145859e62', '1ccd4195-4b2c-5e0f-a8e6-7b55df189a88', 'Q4',  2, 10, 'long', NULL),
    ('45f6ce64-5eb0-58a4-b1d7-36874df7d891', '9e53c22d-fa32-5c2d-8ada-d7770f93cbc6', 'Q5',  1, 10, 'long', NULL),
    ('b215571c-cc97-585a-a79b-00e26d2daafe', '9e53c22d-fa32-5c2d-8ada-d7770f93cbc6', 'Q6',  2, 10, 'long', NULL),
    ('8c37d592-5f0c-5d02-9c1c-a5e6f1925889', '9e53c22d-fa32-5c2d-8ada-d7770f93cbc6', 'Q6a', 1, 6,  'long', 'b215571c-cc97-585a-a79b-00e26d2daafe'),
    ('6a85c357-7f1c-5a83-a74d-93a60096d960', '9e53c22d-fa32-5c2d-8ada-d7770f93cbc6', 'Q6b', 2, 4,  'long', 'b215571c-cc97-585a-a79b-00e26d2daafe'),
    ('b86cbb36-d41a-5e84-9d14-c5f7c2734a9d', '37f65f48-1e99-5705-b97d-ab49fedc7419', 'Q7',  1, 10, 'long', NULL),
    ('f711233d-4c2b-5612-88be-b057244f4d90', '37f65f48-1e99-5705-b97d-ab49fedc7419', 'Q7a', 1, 5,  'long', 'b86cbb36-d41a-5e84-9d14-c5f7c2734a9d'),
    ('259101f8-5f0d-504e-92cd-f2520036dc74', '37f65f48-1e99-5705-b97d-ab49fedc7419', 'Q7b', 2, 5,  'long', 'b86cbb36-d41a-5e84-9d14-c5f7c2734a9d'),
    ('e0f920e7-7233-55a6-bf41-5d3799d8f5d8', '37f65f48-1e99-5705-b97d-ab49fedc7419', 'Q8',  2, 10, 'long', NULL),
    ('bf644b62-a740-5014-8d75-d0d71ece72c1', '37f65f48-1e99-5705-b97d-ab49fedc7419', 'Q8a', 1, 5,  'long', 'e0f920e7-7233-55a6-bf41-5d3799d8f5d8'),
    ('71531400-9519-552c-94f2-7b54fe36f4e5', '37f65f48-1e99-5705-b97d-ab49fedc7419', 'Q8b', 2, 5,  'long', 'e0f920e7-7233-55a6-bf41-5d3799d8f5d8');

-- ── Glossary terms (used directly by core/diagram_evaluator.py's fuzzy
--    label matching against the diagram fixture below — kept verbatim) ──
INSERT INTO glossary_terms (term_id, canonical_term, aliases) VALUES
    ('99990010-0010-0010-0010-999900109999', 'CPU',            ARRAY['Central Processing Unit','processor']),
    ('99990011-0011-0011-0011-999900119999', 'Memory',         ARRAY['RAM','main memory']),
    ('99990012-0012-0012-0012-999900129999', 'Bus',            ARRAY['data bus','system bus']),
    ('88880010-0010-0010-0010-888800109999', 'Input & Output', ARRAY['I/O','IO','Input Output','Input/Output']),
    ('88880011-0011-0011-0011-888800119999', 'Control Bus',    ARRAY['control bus','control lines']),
    ('88880012-0012-0012-0012-888800129999', 'Address Bus',    ARRAY['address bus','address lines']),
    ('88880013-0013-0013-0013-888800139999', 'Data Bus',       ARRAY['data bus','data lines']),
    ('88880014-0014-0014-0014-888800149999', 'System Bus',     ARRAY['system bus architecture','bus architecture']);

-- ── Fixture 1: diagram question + answer (verified working end-to-end via
--    scripts/run_diagram_eval_demo.sh — kept byte-for-byte identical so
--    that script needs zero changes after this reset) ────────────────────
INSERT INTO questions (question_id, status, source_type, style, marks_max, question_group_id, content) VALUES
    ('88880001-0001-0001-0001-888800019999', 'live', 'manual', 'long', 10,
     gen_random_uuid(),
     'Draw a labeled block diagram of the system bus architecture. Show the CPU, Memory, and Input & Output units, and how each is connected to the Control Bus, Address Bus, and Data Bus (grouped together as the System Bus).');

INSERT INTO topic_links (entity_type, entity_id, topic_id) VALUES
    ('question', '88880001-0001-0001-0001-888800019999', 'a1a1a1a1-0001-0001-0001-a1a1a1a1a1a1');

INSERT INTO content_assets (asset_id, asset_type, blob_url, structured_data) VALUES
    ('1aef8f91-dc13-4f15-b1e1-e2df54df087a', 'diagram', NULL, '{
        "schema_version": 1,
        "nodes": [
            {"node_id": "n1", "label": "CPU"},
            {"node_id": "n2", "label": "Memory"},
            {"node_id": "n3", "label": "Input & Output"},
            {"node_id": "n4", "label": "Control Bus"},
            {"node_id": "n5", "label": "Address Bus"},
            {"node_id": "n6", "label": "Data Bus"},
            {"node_id": "n7", "label": "System Bus"}
        ],
        "edges": [
            {"edge_id": "e1", "from_node": "n1", "to_node": "n4", "label": null},
            {"edge_id": "e2", "from_node": "n1", "to_node": "n5", "label": null},
            {"edge_id": "e3", "from_node": "n1", "to_node": "n6", "label": null},
            {"edge_id": "e4", "from_node": "n2", "to_node": "n4", "label": null},
            {"edge_id": "e5", "from_node": "n2", "to_node": "n5", "label": null},
            {"edge_id": "e6", "from_node": "n2", "to_node": "n6", "label": null},
            {"edge_id": "e7", "from_node": "n3", "to_node": "n4", "label": null},
            {"edge_id": "e8", "from_node": "n3", "to_node": "n5", "label": null},
            {"edge_id": "e9", "from_node": "n3", "to_node": "n6", "label": null}
        ]
    }'::json);

INSERT INTO question_asset_links (asset_id, role, question_id) VALUES
    ('1aef8f91-dc13-4f15-b1e1-e2df54df087a', 'question_source', '88880001-0001-0001-0001-888800019999');

INSERT INTO answers (answer_id, question_id, student_id, exam_id, source_scan_url, status, college_id) VALUES
    ('88880003-0003-0003-0003-888800039999', '88880001-0001-0001-0001-888800019999',
     'cccccccc-0001-0001-0001-cccccccccccc', 'dddddddd-0001-0001-0001-dddddddddddd',
     'dummy-storage/scans/alice_system_bus_answer.png', 'pending_evaluation',
     '11111111-1111-1111-1111-111111111111');

INSERT INTO answer_blocks (block_id, answer_id, block_type, blob_url, sequence_order) VALUES
    ('1d9115d4-cd43-4a53-9681-a303f4d2b0e7', '88880003-0003-0003-0003-888800039999', 'diagram',
     'ai-evaluation/content-assets/test-scans/68d32aca-f24f-44b4-b59a-e5b5df70a7b4.jpeg', 2);

-- ── Fixture 2: a simple text question + answer, for evaluate_answer.py —
--    no MinIO/OCR involved, fastest possible smoke test of the text path.
INSERT INTO questions (question_id, status, source_type, style, marks_max, question_group_id, content) VALUES
    ('a0a0a0a0-0001-0001-0001-a0a0a0a0a0a0', 'live', 'manual', 'short', 5,
     gen_random_uuid(),
     'Explain the difference between a stack and a queue.');

INSERT INTO topic_links (entity_type, entity_id, topic_id) VALUES
    ('question', 'a0a0a0a0-0001-0001-0001-a0a0a0a0a0a0', 'a1a1a1a1-0002-0002-0002-a1a1a1a1a1a1');

INSERT INTO reference_answer_variants (variant_id, question_id, variant_type, content, is_current) VALUES
    ('a0a0a0a0-0002-0002-0002-a0a0a0a0a0a0', 'a0a0a0a0-0001-0001-0001-a0a0a0a0a0a0', 'short',
     'A stack is a LIFO (last-in, first-out) structure where elements are added and removed from the same end. A queue is a FIFO (first-in, first-out) structure where elements are added at one end and removed from the other.',
     true);

INSERT INTO answers (answer_id, question_id, student_id, exam_id, source_scan_url, text_extracted, status, college_id) VALUES
    ('a0a0a0a0-0003-0003-0003-a0a0a0a0a0a0', 'a0a0a0a0-0001-0001-0001-a0a0a0a0a0a0',
     'cccccccc-0002-0002-0002-cccccccccccc', 'dddddddd-0001-0001-0001-dddddddddddd',
     'dummy-storage/scans/bob_stack_queue_answer.png',
     'A stack follows LIFO order, meaning the last element added is the first removed. A queue follows FIFO order, where the first element added is the first removed.',
     'pending_evaluation', '11111111-1111-1111-1111-111111111111');

INSERT INTO answer_blocks (block_id, answer_id, block_type, content, sequence_order) VALUES
    (gen_random_uuid(), 'a0a0a0a0-0003-0003-0003-a0a0a0a0a0a0', 'text',
     'A stack follows LIFO order, meaning the last element added is the first removed. A queue follows FIFO order, where the first element added is the first removed.',
     1);
