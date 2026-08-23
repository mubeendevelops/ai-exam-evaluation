-- =============================================================================
-- Migration 006 — Paper Pattern Schema  (Task 2a / 2b)
-- =============================================================================
--
-- What this adds
-- ──────────────
-- Three tables describing a reusable exam paper template (pattern):
--
--   paper_patterns    – top-level template record
--   pattern_sections   – ordered groups of question slots within a pattern
--   pattern_slots       – individual question positions (or sub-parts)
--
-- Design decisions (from PROJECT_CONTEXT.md — Task 2a/2b, as decided)
-- ─────────────────────────────────────────────────────────────────
-- ① Patterns are SHARED across all colleges → NO college_id on any table here.
--
-- ② choose_count (how many questions a student must answer in an optional
--   section) is NOT stored here. It is set per paper-generation run.
--   The pattern only records whether a section is mandatory or optional.
--   Rationale: the same pattern can be reused with "answer 1 of 2" one time
--   and "answer 2 of 3" another time, without duplicating the pattern.
--
-- ③ created_by is nullable:
--      NULL            → platform system template (owned by no one)
--      reviewer_id     → teacher-owned pattern
--
-- ④ Sub-questions (Q2a / Q2b, Q6a / Q6b, etc.) are modelled via
--   parent_slot_id self-reference on pattern_slots.
--   A parent slot's marks field should equal the SUM of its children's marks.
--   This is a soft invariant, validated by the loader script
--   (scripts/load_paper_pattern.py), not enforced by a DB trigger — marks
--   are occasionally adjusted during pattern editing before children exist.
--   A trigger DOES enforce that a parent slot belongs to the same section
--   (see trg_slot_parent_same_section below) since that one is a hard
--   structural rule with no legitimate exception.
--
-- ⑤ style CHECK mirrors questions.style in 002_question_schema.sql.
--   No shared domain/type is introduced, to avoid altering the existing
--   questions table.
--
-- Deliberately NOT included
-- ──────────────────────────
-- A topic-constraint junction table (e.g. "this slot only accepts questions
-- from topic X") was considered and dropped — it was never asked for.
-- Add it as its own migration if/when that requirement is actually decided.
--
-- Example paper structure (CIA 2 — AIML 22CS53, DSCE)
-- ──────────────────────────────────────────────────────
--   Section A  [mandatory]
--     Slot Q1  (long, 10M)
--     Slot Q2  (long, 10M)  ← parent
--       Slot Q2a  (long,  3M)
--       Slot Q2b  (long,  7M)
--   Section B  [optional, choose_count=1 at generation time]
--     Slot Q3  (long, 10M)
--     Slot Q4  (long, 10M)
--   Section C  [optional, choose_count=1]
--     Slot Q5  (long, 10M)
--     Slot Q6  (long, 10M)  ← parent
--       Slot Q6a  (long,  6M)
--       Slot Q6b  (long,  4M)
--   Section D  [optional, choose_count=1]
--     Slot Q7  (long, 10M)  ← parent
--       Slot Q7a  (long,  5M)
--       Slot Q7b  (long,  5M)
--     Slot Q8  (long, 10M)  ← parent
--       Slot Q8a  (long,  5M)
--       Slot Q8b  (long,  5M)
--
-- Run AFTER: 001 through 005.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. paper_patterns
-- -----------------------------------------------------------------------------

CREATE TABLE paper_patterns (
    pattern_id      UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT            NOT NULL,
    description     TEXT,

    -- total_marks = sum of all mandatory top-level slot marks
    --               + one representative slot's marks from each optional
    --               section (the loader computes this assuming equal-marks
    --               alternatives within an optional section, which holds
    --               for every pattern seen so far — see load_paper_pattern.py).
    total_marks     REAL            NOT NULL CHECK (total_marks > 0),

    -- Informational — not a FK, just a label (e.g. '22CS53').
    -- The same pattern may be reused for the same course across semesters.
    course_code     TEXT,

    -- NULL = platform system template; non-NULL = teacher-owned.
    created_by      UUID            REFERENCES reviewers(reviewer_id)
                                        ON DELETE SET NULL,

    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- Soft-delete: retired patterns remain for historical reference.
    is_active       BOOLEAN         NOT NULL DEFAULT TRUE
);

COMMENT ON TABLE  paper_patterns IS
    'Reusable exam paper templates. Shared across all colleges (no college_id).';
COMMENT ON COLUMN paper_patterns.created_by IS
    'NULL = system template; non-NULL = teacher-owned. '
    'References reviewers because teachers are reviewers.';
COMMENT ON COLUMN paper_patterns.total_marks IS
    'Pre-computed total assuming choose_count=1 per optional section. '
    'Actual total at generation time may differ if choose_count > 1.';
COMMENT ON COLUMN paper_patterns.is_active IS
    'FALSE = retired pattern. Kept for historical reference; not selectable for new papers.';


-- -----------------------------------------------------------------------------
-- 2. pattern_sections
-- -----------------------------------------------------------------------------

-- A section is an ordered group of question slots.
--   • Mandatory section  → every slot must be assigned a question.
--   • Optional section   → student answers choose_count of the slots.
--                          choose_count is NOT stored here (see design note ②).

CREATE TABLE pattern_sections (
    section_id      UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    pattern_id      UUID    NOT NULL
                            REFERENCES paper_patterns(pattern_id)
                            ON DELETE CASCADE,

    -- Human-readable label printed on the exam paper.
    -- e.g. 'Part A – Compulsory', 'Unit II – Choice', 'Section B'
    section_label   TEXT    NOT NULL,

    -- 1-based position within the pattern. Determines print order.
    section_order   INT     NOT NULL CHECK (section_order >= 1),

    -- TRUE  → all slots in this section are required.
    -- FALSE → student answers choose_count slots (set at paper-generation time).
    is_mandatory    BOOLEAN NOT NULL DEFAULT TRUE,

    UNIQUE (pattern_id, section_order)
);

COMMENT ON TABLE  pattern_sections IS
    'Ordered question groups within a pattern. '
    'Mandatory sections: all slots answered. '
    'Optional sections: choose_count slots answered (set at generation time).';
COMMENT ON COLUMN pattern_sections.is_mandatory IS
    'TRUE = all slots required. '
    'FALSE = student picks choose_count (stored on the generated paper, not here).';


-- -----------------------------------------------------------------------------
-- 3. pattern_slots
-- -----------------------------------------------------------------------------

-- A slot is a single question position (or a sub-part position) in the exam.
-- Top-level slots represent Q1, Q2, Q3 …
-- Sub-part slots represent Q2a, Q2b, Q6a … and have parent_slot_id set.

CREATE TABLE pattern_slots (
    slot_id         UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    section_id      UUID    NOT NULL
                            REFERENCES pattern_sections(section_id)
                            ON DELETE CASCADE,

    -- Display label printed on the exam paper: 'Q1', '2a', '7b', etc.
    slot_label      TEXT,

    -- 1-based position within the section (or within parent slot for sub-parts).
    slot_order      INT     NOT NULL CHECK (slot_order >= 1),

    -- Marks allocated to this slot (or sub-part).
    -- For parent slots: equals sum of children's marks (validated by loader).
    -- For leaf slots:   the actual marks for that question/sub-question.
    marks           REAL    NOT NULL CHECK (marks > 0),

    -- Question style expected for this slot.
    -- Must stay in sync with questions.style in 002_question_schema.sql.
    style           TEXT    NOT NULL
                    CHECK (style IN ('long', 'short', 'one_word', 'mcq')),

    -- Non-NULL for sub-part slots (Q2a, Q2b, Q6a …).
    -- Parent must belong to the SAME section (enforced by trigger below).
    -- ON DELETE CASCADE: removing a parent removes its sub-parts too.
    parent_slot_id  UUID    REFERENCES pattern_slots(slot_id)
                                ON DELETE CASCADE

    -- NOTE: no table-level UNIQUE(section_id, slot_order) here.
    -- slot_order must be unique among SIBLINGS, not across the whole
    -- section — a sub-part's order (Q2a=1, Q2b=2) legitimately restarts
    -- at 1 while sharing section_id with top-level slots. See the two
    -- partial unique indexes below, which scope ordering correctly:
    -- one for top-level slots (parent_slot_id IS NULL), one for children
    -- (scoped per parent_slot_id).
);

COMMENT ON TABLE  pattern_slots IS
    'Individual question positions in a paper pattern. '
    'Sub-parts (Q2a, Q2b) use parent_slot_id to reference their parent slot.';
COMMENT ON COLUMN pattern_slots.parent_slot_id IS
    'Set for sub-part slots. Parent must be in the same section. '
    'Parent slot marks = sum of children marks (validated by loader, not a trigger).';
COMMENT ON COLUMN pattern_slots.marks IS
    'For parent slots: total marks (= sum of sub-part marks). '
    'For leaf slots: marks awarded for answering this question/sub-question.';


-- Guard: parent_slot_id must reference a slot in the SAME section.
-- Without this, a slot in Section B could claim a parent in Section A,
-- which makes no structural sense and would break traversal queries.

CREATE OR REPLACE FUNCTION trg_fn_slot_parent_same_section()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.parent_slot_id IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1
            FROM   pattern_slots
            WHERE  slot_id    = NEW.parent_slot_id
              AND  section_id = NEW.section_id
        ) THEN
            RAISE EXCEPTION
                'pattern_slots: parent_slot_id (%) must belong to '
                'the same section as slot (%) — section %',
                NEW.parent_slot_id, NEW.slot_id, NEW.section_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_slot_parent_same_section
    BEFORE INSERT OR UPDATE ON pattern_slots
    FOR EACH ROW EXECUTE FUNCTION trg_fn_slot_parent_same_section();


-- Ordering uniqueness, scoped correctly by sibling group.
--
-- Top-level slots (Q1, Q2, Q3 …): slot_order must be unique within the
-- section. Postgres treats NULL != NULL for uniqueness purposes, so a
-- partial index (WHERE parent_slot_id IS NULL) is required — a plain
-- UNIQUE(section_id, parent_slot_id, slot_order) column constraint would
-- silently allow duplicate top-level orders, since every top-level row
-- has parent_slot_id = NULL and NULLs never collide with each other.

CREATE UNIQUE INDEX idx_pattern_slots_top_level_order
    ON pattern_slots (section_id, slot_order)
    WHERE parent_slot_id IS NULL;

-- Sub-part slots (Q2a, Q2b, Q6a …): slot_order must be unique within
-- their specific parent, independently of any other slot's ordering.

CREATE UNIQUE INDEX idx_pattern_slots_child_order
    ON pattern_slots (parent_slot_id, slot_order)
    WHERE parent_slot_id IS NOT NULL;


-- -----------------------------------------------------------------------------
-- 4. Indexes
-- -----------------------------------------------------------------------------

-- Pattern lookup
CREATE INDEX idx_paper_patterns_active
    ON paper_patterns (is_active)
    WHERE is_active;                          -- only live patterns need fast lookup

CREATE INDEX idx_paper_patterns_creator
    ON paper_patterns (created_by)
    WHERE created_by IS NOT NULL;             -- system templates (NULL) rarely queried by creator

-- Section traversal
CREATE INDEX idx_pattern_sections_pattern
    ON pattern_sections (pattern_id, section_order);

-- Slot traversal
CREATE INDEX idx_pattern_slots_section
    ON pattern_slots (section_id, slot_order);

CREATE INDEX idx_pattern_slots_parent
    ON pattern_slots (parent_slot_id)
    WHERE parent_slot_id IS NOT NULL;         -- partial: only sub-part slots


-- =============================================================================
-- Reference: how to query a full pattern (for paper generation or display)
-- =============================================================================
--
--   SELECT
--       ps.section_order,
--       ps.section_label,
--       ps.is_mandatory,
--       psl.slot_label,
--       psl.slot_order,
--       psl.marks,
--       psl.style,
--       psl.parent_slot_id
--   FROM   pattern_sections   ps
--   JOIN   pattern_slots      psl ON psl.section_id = ps.section_id
--   WHERE  ps.pattern_id = '<pattern_uuid>'
--   ORDER  BY ps.section_order, psl.parent_slot_id NULLS FIRST, psl.slot_order;
--
-- (scripts/view_pattern.py wraps this and renders it as a nested tree.)
-- =============================================================================
