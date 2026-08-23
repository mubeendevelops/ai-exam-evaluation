-- ============================================================================
-- scripts/seed_example_paragraphs.sql — inserts a handful of example
-- paragraphs (and a couple of topic tags) for local testing of
-- generate_questions.py. Dev/testing convenience only — not part of the
-- real content-ingestion pipeline (teachers uploading real material is a
-- separate, not-yet-built flow).
--
-- Idempotent: fixed UUIDs, ON CONFLICT DO UPDATE/NOTHING throughout, so
-- re-running this script upserts instead of duplicating rows — same
-- deterministic-ID spirit as load_exam_bank.py's det_uuid, just as literal
-- UUIDs here since this is plain SQL, not a Python script computing uuid5.
--
-- Usage:
--   psql -h $PGHOST -U $PGUSER -d $PGDATABASE -f scripts/seed_example_paragraphs.sql
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. photosynthesis (tagged: Photosynthesis)
-- ----------------------------------------------------------------------------
INSERT INTO paragraphs (paragraph_id, content, source_document, status)
VALUES (
    '7977786e-be58-52b9-8d6e-4a7aa0bb63c9',
    'Photosynthesis is the process by which green plants convert light energy '
    'into chemical energy stored in glucose. It occurs in the chloroplasts and '
    'requires carbon dioxide, water, and sunlight, producing oxygen as a '
    'byproduct.',
    'biology_notes_ch4.docx',
    'active'
)
ON CONFLICT (paragraph_id) DO UPDATE
    SET content = EXCLUDED.content,
        source_document = EXCLUDED.source_document;

INSERT INTO topics (topic_id, name)
VALUES ('a5f5c1b1-3b7a-5c3d-8a0e-1c2d3e4f5a6b', 'Photosynthesis')
ON CONFLICT (topic_id) DO NOTHING;

INSERT INTO topic_links (link_id, entity_type, entity_id, topic_id)
VALUES (
    'b1c2d3e4-f5a6-5b7c-9d0e-1f2a3b4c5d6e', 'paragraph',
    '7977786e-be58-52b9-8d6e-4a7aa0bb63c9', 'a5f5c1b1-3b7a-5c3d-8a0e-1c2d3e4f5a6b'
)
ON CONFLICT (link_id) DO NOTHING;

-- ----------------------------------------------------------------------------
-- 2. tcp_handshake (tagged: TCP/IP Networking)
-- ----------------------------------------------------------------------------
INSERT INTO paragraphs (paragraph_id, content, source_document, status)
VALUES (
    '081cf05c-5f9a-5bd5-8159-669ae5e19833',
    'The TCP three-way handshake establishes a connection between a client and '
    'a server before any data is transferred. The client sends a SYN packet, '
    'the server responds with a SYN-ACK, and the client replies with an ACK, '
    'after which the connection is considered established.',
    'networking_unit2.docx',
    'active'
)
ON CONFLICT (paragraph_id) DO UPDATE
    SET content = EXCLUDED.content,
        source_document = EXCLUDED.source_document;

INSERT INTO topics (topic_id, name)
VALUES ('c2d3e4f5-a6b7-5c8d-0e1f-2a3b4c5d6e7f', 'TCP/IP Networking')
ON CONFLICT (topic_id) DO NOTHING;

INSERT INTO topic_links (link_id, entity_type, entity_id, topic_id)
VALUES (
    'd3e4f5a6-b7c8-5d9e-1f2a-3b4c5d6e7f8a', 'paragraph',
    '081cf05c-5f9a-5bd5-8159-669ae5e19833', 'c2d3e4f5-a6b7-5c8d-0e1f-2a3b4c5d6e7f'
)
ON CONFLICT (link_id) DO NOTHING;

-- ----------------------------------------------------------------------------
-- 3. normalization (tagged: Database Normalization)
-- ----------------------------------------------------------------------------
INSERT INTO paragraphs (paragraph_id, content, source_document, status)
VALUES (
    '38bce224-f028-5b6b-940b-2d09908f935b',
    'Database normalization is the process of organizing tables to reduce data '
    'redundancy and improve data integrity. First normal form (1NF) requires '
    'atomic column values, second normal form (2NF) removes partial '
    'dependencies on a composite key, and third normal form (3NF) removes '
    'transitive dependencies.',
    'dbms_unit3.docx',
    'active'
)
ON CONFLICT (paragraph_id) DO UPDATE
    SET content = EXCLUDED.content,
        source_document = EXCLUDED.source_document;

INSERT INTO topics (topic_id, name)
VALUES ('e4f5a6b7-c8d9-5e0f-2a3b-4c5d6e7f8a9b', 'Database Normalization')
ON CONFLICT (topic_id) DO NOTHING;

INSERT INTO topic_links (link_id, entity_type, entity_id, topic_id)
VALUES (
    'f5a6b7c8-d9e0-5f1a-3b4c-5d6e7f8a9b0c', 'paragraph',
    '38bce224-f028-5b6b-940b-2d09908f935b', 'e4f5a6b7-c8d9-5e0f-2a3b-4c5d6e7f8a9b'
)
ON CONFLICT (link_id) DO NOTHING;

-- ----------------------------------------------------------------------------
-- 4. french_revolution — intentionally UNTAGGED, to test the no-topic path
-- ----------------------------------------------------------------------------
INSERT INTO paragraphs (paragraph_id, content, source_document, status)
VALUES (
    '5a1869f6-a7b4-5cd4-abb1-7f7f4437a417',
    'The French Revolution began in 1789, driven by widespread famine, high '
    'taxation, and resentment of the monarchy''s absolute power. It led to the '
    'abolition of the monarchy, the rise of Napoleon Bonaparte, and lasting '
    'changes to political structures across Europe.',
    'world_history_ch7.docx',
    'active'
)
ON CONFLICT (paragraph_id) DO UPDATE
    SET content = EXCLUDED.content,
        source_document = EXCLUDED.source_document;

COMMIT;

-- paragraph_id values for use with generate_questions.py:
--   photosynthesis       7977786e-be58-52b9-8d6e-4a7aa0bb63c9
--   tcp_handshake         081cf05c-5f9a-5bd5-8159-669ae5e19833
--   normalization          38bce224-f028-5b6b-940b-2d09908f935b
--   french_revolution     5a1869f6-a7b4-5cd4-abb1-7f7f4437a417
