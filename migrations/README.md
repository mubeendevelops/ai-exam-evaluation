# Answer Evaluation Schema — Migration Notes

## Migration file

**`001_answer_schema.sql`** — single idempotent script; safe to re-run.

---

## Placeholder (stub) tables

Two tables from the **Question schema** are stubbed out with the minimum
columns needed so that foreign keys in the Answer schema resolve:

| Stub table                  | Columns kept                                      | Why                                                                                                                                                 |
| --------------------------- | ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `questions`                 | `question_id` (PK), `status` (enum)               | FK target for `answers.question_id`; `status` is required by the trigger that enforces "only live questions can receive answers."                   |
| `reference_answer_variants` | `variant_id` (PK), `question_id` (FK → questions) | FK target for `evaluation_results.reference_answer_variant_id`; `question_id` is required by the trigger that cross-checks question-id consistency. |

> **When the real Question schema migration lands**, these two tables must be
> DROP-and-recreated (or ALTER-ed) to carry the full column set defined in
> `question-schema-design.md`. Both triggers reference the same column names
> that the full schema uses (`question_id`, `status`, `variant_id`), so they
> should continue to work — but verify after the swap.

---

## Assumptions

1. **UUID primary keys** — all PKs use `UUID DEFAULT gen_random_uuid()`.
   The design docs specify "PK" without prescribing the type; UUID was chosen
   for globally unique, index-friendly identifiers compatible with distributed
   inserts (e.g., bulk upload from multiple scan stations).

2. **TIMESTAMPTZ** — all timestamp columns use `TIMESTAMPTZ` (not bare
   `TIMESTAMP`) so values are unambiguous across time zones.

3. **`text_extracted` NOT NULL** — the design doc does not mark this nullable.
   If an answer has not yet been OCR'd, the row should not exist yet (the
   digitization pipeline creates the `answers` row _after_ OCR completes).
   If in practice the row must be created before OCR runs, this column will
   need to be changed to nullable.

4. **`DOUBLE PRECISION` for scores/marks** — `float` in the design doc is
   mapped to Postgres `DOUBLE PRECISION` (8-byte IEEE 754). If only 4-byte
   precision is needed, `REAL` can be substituted.

5. **No cascading deletes** — all FK constraints use the Postgres default
   (`NO ACTION` / `RESTRICT`). This aligns with the §7 recommendation that
   questions with existing answers should not be hard-deletable.

6. **`old_status` / `new_status` as TEXT** — `answer_status_history` stores
   these as plain text (matching the design doc) rather than the `answer_status`
   enum. This avoids breakage if enum values are added later and historical
   rows reference values that no longer exist.

---

## Relevant open items (from PROJECT_CONTEXT.md §7)

The following open decisions from the project context document directly affect
this schema. They are **flagged here, not resolved** — confirm with the product
owner before acting on any of them.

| §7 item                            | Impact on this schema                                                                                                                                                                                                                                                                  |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Answer correction / versioning** | `answers.text_extracted` has no version history. If OCR-correction becomes a workflow, a revision mechanism (e.g. an `answer_text_versions` table) will be needed.                                                                                                                     |
| **Student resubmission**           | Nothing in this schema prevents multiple `answers` rows for the same `(student_id, question_id)` pair. If exactly-one-answer is the rule, a `UNIQUE(student_id, question_id)` constraint (possibly scoped to `exam_id`) should be added. If resubmission is allowed, no change needed. |
| **Question deletion cascade**      | FK on `answers.question_id` currently uses default `RESTRICT` (no cascade). This matches the §7 recommendation but is not a confirmed decision.                                                                                                                                        |
| **Re-opening a finalized answer**  | The `answer_status` enum has no transition back from `finalized`. If re-evaluation is required, a new enum value or a redefinition of `finalized` is needed — this will require an `ALTER TYPE` migration.                                                                             |
| **`exam_id` optionality**          | `answers.exam_id` is `NOT NULL` in this migration. There is no path for practice/non-exam answers. If that becomes in-scope, the column must be made nullable and the FK adjusted.                                                                                                     |

# Question Knowledge Repository / Generation schema — migration notes

File: `002_question_schema.sql`
**Prerequisite:** `001_answer_schema.sql` must already be applied — this
migration extends tables it created.

## How this reconciles with the Answer schema's placeholder tables

`001_answer_schema.sql` created two minimal stand-ins:

- `questions(question_id PK, status)`
- `reference_answer_variants(variant_id PK, question_id FK->questions)`

`answers.question_id` and `evaluation_results.reference_answer_variant_id`
already have live FK constraints pointing at these tables, and two triggers
(`trg_answers_question_must_be_live`,
`trg_evaluation_variant_matches_answer_question`) already query them.

**Chosen approach: extend in place via `ALTER TABLE ... ADD COLUMN`, not
`DROP TABLE` + `CREATE TABLE`.** A literal `DROP TABLE questions` would do
one of two things: fail outright (existing FK from `answers` blocks it), or
succeed with `CASCADE` and silently drop `answers.question_id`'s FK
constraint (and the triggers, since they reference the table) along with it
— requiring them to be recreated and re-verified, and risking any existing
data. Extending the same table object instead means:

- the PK column (`question_id` / `variant_id`), its type, and its existing
  constraints never change,
- the FK constraints on `answers` and `evaluation_results` are never
  touched, let alone dropped and recreated,
- both integrity triggers from 001 keep working with zero changes, because
  they were never pointed at a different object.

This satisfies "recreate them fully... so the existing FKs... remain valid"
more safely than a literal drop/recreate would.

## Assumptions made (not specified in the source docs)

- **`question_keywords` primary key**: the doc lists `question_id`,
  `keyword_id`, `weight` with no stated PK. Assumed a composite PK on
  `(question_id, keyword_id)` — one weight per keyword per question. If a
  keyword can legitimately be attached to the same question more than once
  with different weights, this assumption is wrong and the PK should be
  dropped in favor of a surrogate key.
- **NOT NULL enforcement on extended placeholder columns is guarded, not
  unconditional.** Adding a `NOT NULL` column via `ALTER TABLE` fails if the
  table already has rows (existing rows get `NULL`, which then violates the
  constraint). The script checks `COUNT(*)` on `questions` and
  `reference_answer_variants` before adding `NOT NULL`; if either table
  already has rows, it emits a `RAISE NOTICE` and leaves those columns
  nullable rather than failing the whole migration. Backfill the data, then
  run the listed `ALTER TABLE ... SET NOT NULL` statements manually. In a
  typical fresh dev/staging environment (placeholders only just created to
  resolve FKs, no real question data yet) both tables are empty and this is
  a no-op — the columns end up `NOT NULL` as the doc specifies.
- **Polymorphic columns stay unconstrained, matching the doc exactly**:
  - `topic_links.entity_id` (points into `paragraphs` or `questions`
    depending on `entity_type`)
  - `questions.source_id` (points into `sentences`/`paragraphs`/
    `content_assets` depending on `source_type`; `NULL` when `manual`)
  - `question_asset_links.question_id` /
    `.reference_answer_variant_id` (populated depending on `role`)

  None of these get a `CHECK` constraint tying the discriminator column to
  the nullability/target rule, even though the doc states the rule in prose.
  This mirrors the same minimal-constraint choice already made for
  `answer_reviews.final_marks` in `001_answer_schema.sql` (nullable per the
  doc's note, not `CHECK`-enforced) — kept consistent rather than
  introducing enforcement in one migration and not the other.

- **`reviewers` is untouched.** No `CREATE TABLE` or `ALTER TABLE` statement
  targets it in this file, per the requirement to reuse it as-is.

## Open items flagged, not resolved (PROJECT_CONTEXT.md §7)

- **Topic hierarchy shape**: `topics.parent_topic_id` stays a single
  nullable self-reference (a tree). No graph DB, no multi-parent support was
  added — per explicit instruction, this is not this migration's call to
  make.
- **Question deletion cascade**: no `ON DELETE` behavior was added to any FK
  targeting `questions` (default `NO ACTION`, i.e. deletion is blocked
  unless the row is otherwise unreferenced). The doc recommends `RESTRICT`
  for `answers.question_id` (already applied in 001) and versioning via
  `supersedes_question_id` / `status = superseded` instead of hard deletes —
  this migration follows that pattern (versioning columns exist, no cascade
  delete anywhere) but doesn't treat it as a confirmed decision.
- **Answer correction/versioning**: the Question schema's own
  `paragraphs.version` / `status` pattern is unrelated to the still-open gap
  on `answers.text_extracted` (no version history) flagged in 001 — that gap
  remains open and is not addressed by this migration.
- **exam_id optionality / student resubmission / re-opening a finalized
  answer / OCR engine choice**: not relevant to this migration's scope
  (Answer-schema-only concerns); still open per 001's README.

Nothing beyond what `question-schema-design.md` and PROJECT_CONTEXT.md §4/§6
specify was added — no extra tables, columns, or constraints, and no
Neo4j/graph DB.

# Multi-tenancy migration — notes

File: `003_multi_tenancy.sql`
**Prerequisite:** `001_answer_schema.sql` and `002_question_schema.sql`
already applied.

## What changed and why

**Question schema — untouched.** All 12 tables stay exactly as built in
`002_question_schema.sql`. No `college_id`, no RLS. This is the shared
question bank every college reads from and (via `question_reviews`)
contributes review activity to.

**Answer schema — every table becomes tenant-scoped.** `colleges` is a new
tenant table (not in the original docs — required to have multi-tenancy at
all). `college_id` was added to `students`, `exams`, `answers`,
`answer_blocks`, `evaluation_results`, `answer_reviews`, and
`answer_status_history`.

**`reviewers` gets a nullable `college_id`.** `NULL` = platform-level
reviewer, free to act on the shared question bank across colleges.
Non-null = tied to one college; the triggers on `answer_reviews` and
`answer_status_history` stop that reviewer from touching another college's
answers, but a college-affiliated reviewer can still do `question_reviews`
on the shared bank if you want teachers contributing to it — that's
intentionally not restricted, since questions are meant to be shared.

## Why shared-schema + RLS instead of schema-per-tenant or DB-per-tenant

- **Separate databases per college** would make sharing the question bank
  impossible without cross-database replication/sync — you can't FK across
  databases, and `answers.question_id → questions.question_id` needs to
  keep working.
- **Separate schemas per college within one database** avoids the
  cross-database problem, but multiplies migration/operational complexity
  linearly with customer count (every DDL change runs N times), and still
  needs a shared `questions` schema referenced from N tenant schemas —
  extra complexity for no isolation benefit RLS doesn't already give you.
- **Shared schema + `college_id` + RLS** is the standard SaaS pattern: one
  set of tables, one migration path, and the database itself refuses to
  return rows outside the caller's tenant — even if application code has a
  bug and forgets a `WHERE college_id = ...` clause.

## `college_id` is derived, not trusted from app input

For `answers`, `answer_blocks`, `evaluation_results`, `answer_reviews`, and
`answer_status_history`, `college_id` is set by a `BEFORE INSERT/UPDATE`
trigger from the parent row (student/exam for `answers`; the answer itself
for the other four) — whatever the app sends in that column is overwritten.
This mirrors the pattern already used for `evaluation_results` /
`trg_evaluation_variant_matches_answer_question` in `001_answer_schema.sql`:
consistency enforced at the database layer, not assumed from the caller.

`answers` additionally rejects the insert/update outright if the referenced
student and exam belong to different colleges — a student can't submit an
answer to another college's exam.

## Row-Level Security — what the application must do

Every request/transaction must set, before running any query:

```sql
SET LOCAL app.current_college_id = '<the requesting college''s UUID>';
```

`SET LOCAL` (not plain `SET`) scopes it to the current transaction, which
matters if you're using a connection pooler like PgBouncer in transaction
mode — the setting won't leak into the next pooled request. If your web
framework runs each request in its own transaction (typical), set this
right after authenticating the request and before any other query.

For cross-tenant admin/support tooling, set instead (or additionally):

```sql
SET LOCAL app.is_platform_admin = 'true';
```

**If `app.current_college_id` is never set, the tenant policy matches
nothing and the caller sees zero rows** (fails closed) rather than
erroring or leaking data — safe default, but also means a forgotten `SET
LOCAL` will look like "no data" bugs in testing, not a security incident.
Worth adding a startup/integration test that asserts this.

`FORCE ROW LEVEL SECURITY` is applied to all seven tables. This matters if
your application connects using the same Postgres role that owns these
tables (common in simpler setups) — without `FORCE`, RLS silently doesn't
apply to the table owner. If you later introduce a dedicated lower-privilege
app role (recommended for defense-in-depth), `FORCE` is harmless to keep.

## Data backfill for existing rows

A `colleges` row with `short_code = 'legacy'` is created (once, idempotently)
to own whatever `students` / `exams` / `answers` rows already existed before
this migration. Real colleges get onboarded as new `colleges` rows by your
application/admin flow afterward; existing legacy data isn't automatically
reassigned — that's a data-migration decision for whoever ran the platform
single-tenant, not something this script should guess at.

## Things worth deciding before this goes to production

- **`colleges` fields are my addition**, not in either source doc — kept
  minimal (`name`, `short_code`, `status`, `created_at`). You'll likely want
  more here soon (billing plan, contact info, subdomain vs. custom domain,
  seat limits) — this migration deliberately doesn't guess at those.
- **`students.roll_number` uniqueness** changed from global to
  `(college_id, roll_number)`. The `DROP CONSTRAINT` uses Postgres's default
  auto-generated constraint name from the original inline `UNIQUE` — verify
  with `\d students` if it turns out to be a no-op in your environment (a
  manually-renamed constraint would need updating in the script).
  `reviewers.email` was left as-is (no uniqueness was specified in the
  original schema either, so nothing to reconcile there).
  A reviewer with `college_id` set could in principle also be paid
  cross-college as an SME on the shared bank — this migration doesn't
  restrict that, only restricts them from reviewing _other colleges'
  answers_.
- **Reporting/analytics that need to cross tenants** (e.g. "average score on
  this shared question across all colleges") will need to run with
  `app.is_platform_admin = 'true'`, or as a separate reporting role with
  `BYPASSRLS` — not addressed here since it's a reporting/analytics-layer
  concern, not a schema one.

# Add question text column — migration notes

File: `004_add_question_text.sql`
**Prerequisite:** `001`, `002`, `003` already applied.

## What this does

Adds a `content` column to the `questions` table. The original schema had
`source_type` and `source_id` pointing at where a question was generated from,
but no column storing the question's actual text. This is critical for:
- **Manual questions** (source_type = 'manual'): have no source to regenerate
  from, so the text must be stored directly.
- **Sourced questions** (source_type = 'sentence'/'paragraph'/etc.): while
  regenerable from source + template, storing the actual text avoids
  regeneration overhead and captures any human edits.

## Guarded NOT NULL enforcement

The column starts nullable. When existing rows are backfilled with content,
re-run this migration or manually run `ALTER TABLE questions ALTER COLUMN
content SET NOT NULL` to make the column mandatory.

---

# Question tree and AI generation flag — migration notes

File: `005_question_tree_and_ai_flag.sql`
**Prerequisite:** `001`, `002`, `003`, `004` already applied.

## What this adds

**`is_ai_generated` (boolean, nullable)** — flags whether a question was
created by an AI system or by a human. Unlike `source_type` (which says WHERE
a question came from), this says WHO created it. Example: a question with
`source_type='paragraph'` and `is_ai_generated=true` means "AI rewrote/
generated this from a paragraph source." NULL = unknown (legacy questions
loaded before this column existed).

**`parent_question_id` (self-referential FK, nullable)** — tracks the
derivation tree: which question a reworded/variant question branches from.
Different from `supersedes_question_id`:
- **`supersedes_question_id`** = strict replacement (original retires, status
  flips to 'superseded')
- **`parent_question_id`** = creative variation (original stays active and
  usable; enables rewording, difficulty variants, style changes)

Multiple children can share the same parent, forming a real derivation tree.

## The `question_tree` view

A convenience view that exposes the full tree structure via a recursive CTE:

```sql
SELECT * FROM question_tree WHERE root_question_id = '<uuid>';
```

Returns: `question_id`, `parent_question_id`, content, marks, status, depth,
`path` (array of ancestor UUIDs), `root_question_id`.

---

# Paper pattern schema — migration notes

File: `006_paper_pattern_schema.sql`
**Prerequisite:** `001`, `002`, `003`, `004`, `005` already applied.

## What this adds

Three linked tables defining reusable exam paper templates:

- **`paper_patterns`** — top-level template record (name, total_marks,
  course_code, created_by, is_active)
- **`pattern_sections`** — ordered groups of question slots
  (section_label, is_mandatory, section_order)
- **`pattern_slots`** — individual question positions
  (slot_label, marks, style, slot_order, parent_slot_id for sub-parts)

## Key design decisions

1. **Shared across all colleges** — no `college_id` on any table. Patterns are
   platform-wide resources, like questions.

2. **`choose_count` lives on generated papers, not patterns** — the pattern
   only records whether a section is mandatory/optional. At generation time,
   you decide "answer 1 of 2" vs "answer 2 of 3" — same pattern, different
   papers.

3. **`created_by` nullable** — NULL means system-owned template; non-NULL
   means teacher-owned.

4. **Sub-questions modeled via self-reference** — Q2a, Q2b are child slots
   with `parent_slot_id` pointing to Q2. Marks soft-invariant: parent marks
   should equal sum of children's (validated by loader script, not a trigger).

5. **Top-level and child slot ordering are independent** — Q2a and Q2b both
   start with order=1 within their parent. Partial unique indexes enforce
   this correctly.

---

# Generated papers schema — migration notes

File: `008_generated_papers.sql`
**Prerequisite:** `001` through `006` already applied. (There is no 007 — see
the index table at the end of this file.)

## What this adds

Three tables linking paper patterns to actual questions:

- **`generated_papers`** — concrete exam paper created from a pattern
  (name, status, pattern_id, generated_by)
- **`paper_sections`** — per-section metadata, including the `choose_count`
  decided at generation time
- **`paper_questions`** — the actual question→slot assignments

## Key design decisions

1. **Shared across all colleges** — no `college_id`, no RLS. Same scope as
   patterns and questions.

2. **`choose_count` is concrete here** — recorded per paper, per section.
   Same pattern can be used multiple ways.

3. **Only 'live' questions allowed** — enforced by trigger
   `trg_paper_question_must_be_live`, mirroring the `answers` table's
   question-status check.

4. **No duplicate questions per paper** — UNIQUE(paper_id, question_id)
   enforced by trigger (cross-section constraint).

5. **One slot filled per paper section** — UNIQUE(paper_section_id, slot_id).

6. **Status flow: draft → finalized** — draft papers can be edited; finalized
   papers are locked. Actual status transitions handled at the script layer.

---

# Evaluation model tracking — migration notes

File: `009_evaluation_model_column.sql`
**Prerequisite:** `001` through `008` already applied.

## What this adds

A single column: `evaluator_model TEXT` on `evaluation_results`.

## Why it exists

`evaluator_type` distinguishes only 'ai' from 'sme'. Task 3 introduced two
distinct AI methods:
- Sentence-transformer embeddings (e.g., 'all-MiniLM-L6-v2')
- LLM prompt-based scoring (e.g., 'qwen/qwen3.6-27b')

Both are `evaluator_type='ai'`, but they're fundamentally different. This
column records which model/method produced each score, enabling:
- Model comparison (embeddings vs. LLM)
- Auditability ("which version scored this?")
- Experimentation ("try model X vs. Y on the same answer set")

NULL for legacy rows or SME scores (no model involved).

---

# Evaluation metrics — migration notes

File: `010_evaluation_metrics_column.sql`
**Prerequisite:** `001` through `009` already applied.

## What this adds

A single column: `metrics JSONB` on `evaluation_results`.

## Why it exists

To compare evaluation models, we capture performance metrics: latency, token
usage, cost. The JSONB column is flexible — different evaluators can log
different metrics.

Example:

```json
{
  "latency_ms": 1500,
  "prompt_tokens": 300,
  "completion_tokens": 150,
  "cost_usd": 0.0045
}
```

---

# Glossary terms for diagram evaluation — migration notes

File: `011_glossary_terms.sql`
**Prerequisite:** `001` through `010` already applied.

## What this adds

A `glossary_terms` table storing canonical vocabulary for diagram-label
normalization. Distinct from the `keywords` table in 002:

| Term | Keywords | Glossary |
|------|----------|----------|
| **Purpose** | Tag a question's relevance to concepts | Normalize noisy OCR output for diagram matching |
| **Structure** | (question_id, keyword_id, weight) | (term_id, canonical_term, aliases) |
| **Scope** | Per-question relevance | Global or topic-scoped vocabulary |

## How it's used

When comparing a student's handwritten diagram against a reference diagram:
1. Extract labels from both via OCR
2. Normalize extracted labels against glossary aliases (case-insensitive,
   fuzzy matching)
3. Compare the normalized labels
4. Report matches/mismatches

Example entry:

```
canonical_term: "Central Processing Unit"
aliases: ["CPU", "processor", "central processor"]
topic_id: NULL  (applies globally)
```

---

# Diagram evaluation support — migration notes

File: `012_diagram_evaluation.sql`
**Prerequisite:** `001` through `011` already applied.

## What this changes

`evaluation_results` (originally for text-answer scoring) becomes polymorphic:
now handles BOTH text answers and diagram answers.

### Before

Every `evaluation_results` row scored a text answer against a
`reference_answer_variant`:
```
evaluation_results.reference_answer_variant_id → reference_answer_variants.variant_id
```

### After

Exactly one of these columns is set per row:
- **`reference_answer_variant_id`** (set for text answers) — unchanged from 001
- **`reference_asset_id`** (new, set for diagram answers) — points to the
  reference diagram in `content_assets`

A CHECK constraint enforces mutual exclusion: never both, never neither.

### Cross-schema integrity

The trigger `fn_check_evaluation_reference_matches_answer_question` (replaces
the original 001-era trigger) now validates both paths:

- If `reference_answer_variant_id` is set: the variant's `question_id` must
  match the answer's `question_id` (unchanged from 001).
- If `reference_asset_id` is set: the asset must be linked to the same
  question via `question_asset_links(role='question_source')`.

### Structured diagram comparison

The full comparison logic (node validation, edge comparison, glossary matches,
anomalies) is NOT given new columns — it lands in the existing `metrics JSONB`
column (from 010), following the pattern of "structured extra detail."

---

## Index and file map

| Migration | What it adds |
|-----------|-------------|
| 001 | Answer schema (stub questions/variants, evaluation_results, answer reviews & status tracking, reviews) |
| 002 | Question schema (questions, reference_answer_variants, keywords, topics, paragraphs, sentences, content_assets, question_asset_links, question_reviews, topic_links) |
| 003 | Multi-tenancy (colleges, RLS policies, college_id on answer-schema tables) |
| 004 | `questions.content` TEXT column |
| 005 | `questions.is_ai_generated` BOOLEAN + `questions.parent_question_id` + `question_tree` view |
| 006 | Paper patterns (`paper_patterns`, `pattern_sections`, `pattern_slots` tables) |
| 007 | *Never existed.* The `pattern_slots` ordering fix that docs once attributed to a `007_fix_pattern_slot_ordering.sql` was folded into 006 before 006 was ever committed. Verified 2026-08-31 (see note below). |
| 008 | Generated papers (`generated_papers`, `paper_sections`, `paper_questions` tables) |
| 009 | `evaluation_results.evaluator_model` TEXT column |
| 010 | `evaluation_results.metrics` JSONB column |
| 011 | Glossary terms (`glossary_terms` table) |
| 012 | Diagram evaluation support (make `evaluation_results` polymorphic over `reference_answer_variant_id` / `reference_asset_id`) |
| 013 | Booklet region provenance on `answer_blocks` (page number, bbox, page image ref, classification label/confidence, review flag) |

### Note on the "missing" migration 007

Earlier revisions of this file, `readme files/SCRIPT_COMMANDS.md` and
`CLAUDE_CONTEXT.md` described a `007_fix_pattern_slot_ordering.sql` that
patched a buggy first cut of 006, and warned that the file was missing from
git. **It was never written.** Resolved 2026-08-31 by three independent
checks:

1. **006's own source.** `006_paper_pattern_schema.sql` already creates both
   correctly-scoped partial unique indexes —
   `idx_pattern_slots_top_level_order` on `(section_id, slot_order) WHERE
   parent_slot_id IS NULL` and `idx_pattern_slots_child_order` on
   `(parent_slot_id, slot_order) WHERE parent_slot_id IS NOT NULL` — and
   carries an in-file comment explaining why a plain
   `UNIQUE(section_id, slot_order)` would be wrong (every top-level row has
   `parent_slot_id = NULL`, and NULLs never collide for uniqueness, so
   top-level ordering would go unenforced).
2. **Git history.** `git log --follow -- migrations/006_paper_pattern_schema.sql`
   returns exactly one commit (`ee6d7f6`, 2026-08-23), and that commit's diff
   already contains both partial indexes. There was never a buggy first cut
   in version control — the fix was folded in before the file was committed.
3. **The live database.** `\d pattern_slots` on `ai_evaluation` shows zero
   drift from 006: both partial unique indexes present, no plain
   `UNIQUE(section_id, slot_order)` constraint, and the
   `trg_slot_parent_same_section` trigger in place.

No 007 is needed and none should be written. 013 has since been written
(booklet region provenance), so the next migration number is **014**.

---

# Booklet region provenance — migration notes

`013_booklet_regions.sql`

## What this adds

Six nullable columns on `answer_blocks`, plus two indexes and two guarded
CHECK constraints:

| column | type | purpose |
|---|---|---|
| `page_number` | `INT` | 1-based page of the source booklet PDF |
| `region_bbox` | `JSONB` | `[x, y, w, h]` in **deskewed page-image** pixels |
| `page_image_url` | `TEXT` | stable `bucket/key` of the full deskewed page |
| `classification_label` | `TEXT` | the raw layout-model label, pre-mapping |
| `classification_confidence` | `REAL` | layout-model confidence, `[0, 1]` |
| `needs_review` | `BOOLEAN NOT NULL DEFAULT false` | flagged for a human |

## Why it exists

Until Task 5, every `answer_blocks` row was created one at a time from a
single pre-cropped image (`scripts/upload_diagram_scan.py`, or the ad-hoc
INSERT behind `run_table_eval_demo.sh`). Booklet ingestion instead produces
many blocks per answer, each cut out of a specific page by a layout model, so
the table needed to record *where a block came from* and *how sure the
classifier was*. Without both, a mis-routed region is indistinguishable from a
correct one after the fact and a reviewer cannot find it on the page.

## Design decisions

**No new `answer_block_type` enum value.** The obvious alternative was an
`'unknown'` block_type for uncertain regions. Rejected: `block_type` is what
plugins dispatch on (`core/plugins/registry.py::plugins_for`), so `'unknown'`
would be a type no plugin supports — silently dropping regions instead of
surfacing them. Uncertainty is a separate axis from kind, so it gets its own
column (`needs_review`) and the block keeps its best guess. This also sidesteps
`ALTER TYPE ... ADD VALUE`, whose new value cannot be used in the transaction
that adds it, and so cannot be made cleanly idempotent inside the
`BEGIN`/`COMMIT` that rule 5 requires.

**`classification_confidence` is not `confidence_score`.** The existing
`confidence_score` is the OCR confidence of the text read out of a block. A
region can be confidently a table and still be read badly; the two failures
have different fixes, so they get different columns.

**Everything nullable.** All six columns are nullable (or defaulted), so every
pre-existing row and writer keeps working with no change — the migration is
purely additive.

## Still open

There is **no FK from `exams` to `generated_papers`**, so a scanned booklet's
exam cannot be resolved to the paper whose `pattern_slots.slot_label` values
("Q1", "Q2a") its detected markers must match against.
`scripts/ingest_booklet.py` therefore takes an explicit `--paper-id`. Adding
`exams.paper_id UUID NULL REFERENCES generated_papers(paper_id)` would close
this, but exam/answer modelling is on the list of open product decisions
(`readme files/PROJECT_CONTEXT.md` §7) and was deliberately not settled here.

Unassigned regions are likewise **not persisted at all** —
`answer_blocks.answer_id` is `NOT NULL`, so an orphan region has nowhere to
live without weakening that FK. They appear in the ingestion report only.

---

# Evaluation job queue — migration notes

**File:** `014_evaluation_jobs.sql`. Run after 001–013. Idempotent
(`CREATE TABLE IF NOT EXISTS`, guarded `CREATE TYPE`, guarded `ADD CONSTRAINT`,
`CREATE INDEX IF NOT EXISTS`, `DROP POLICY IF EXISTS` before each
`CREATE POLICY`), wrapped in `BEGIN`/`COMMIT` per rule 5. Purely additive — it
touches no existing table, column, or row.

## What this adds

One table, `evaluation_jobs`, and one enum, `evaluation_job_status`
(`queued | running | succeeded | failed`).

| column | type | notes |
|---|---|---|
| `job_id` | UUID PK | `gen_random_uuid()` |
| `college_id` | UUID **NOT NULL** → `colleges` | many jobs → one college, `ON DELETE RESTRICT` |
| `job_type` | TEXT NOT NULL | e.g. `booklet_evaluation` |
| `status` | `evaluation_job_status` NOT NULL | default `queued` |
| `payload` | JSONB NOT NULL | worker input; default `{}` |
| `result` | JSONB NULL | run report, once terminal |
| `error` | TEXT NULL | required when `status='failed'` |
| `attempts` | INT NOT NULL | default 0, incremented on each **claim** |
| `created_at` | TIMESTAMPTZ NOT NULL | `now()` |
| `started_at` / `finished_at` | TIMESTAMPTZ NULL | see the state-machine constraints |

RLS: `ENABLE` + `FORCE`, with the same `tenant_isolation` /
`platform_admin_bypass` policy pair migration 003 puts on the other
answer-schema tables.

## Why it exists

Booklet evaluation takes **minutes** (§7D — OCR, layout detection and LLM
calls per region), so the API cannot do it inside a request. The job row is the
handoff between `POST /api/v1/upload` and `scripts/run_job_worker.py`.

**Why not Redis.** The design doc names Redis for job queueing;
`CLAUDE_CONTEXT.md` §2 and §10 both record that it is implemented *nowhere* —
no client, no helper, no code. Choosing Postgres here buys three things Redis
cannot: the same RLS tenant isolation every other answer-schema table already
has (Redis has none, so it would be hand-rolled a second time), inclusion in
the existing backup, and **transactional consistency with the rows a job reads
and writes** — a job's state change and the `evaluation_results` row it
produces commit together or not at all.

`SELECT ... FOR UPDATE SKIP LOCKED` is what makes it a queue rather than a
table people race on: a row locked by one worker's open transaction is *skipped*
by every other worker's claim rather than blocking it.

**Scale honesty:** right at this project's scale (one Postgres, a handful of
workers, minute-long jobs, queue depth in the tens). Not a general broker
replacement — no fan-out, no pub/sub, no delayed-retry backoff, and the long
row locks would matter if jobs were milliseconds. Revisit if those stop being
true; not merely because a broker is conventional.

## Design decisions

**Status is an enum, `job_type` is TEXT.** The four statuses *are* the state
machine, and adding one is a design change. Job *kinds* are expected to be
added routinely (pending-text batches, re-scoring runs), and an enum would make
each need a migration plus `ALTER TYPE ... ADD VALUE` — which cannot use its new
value in the transaction that adds it, so it cannot be made idempotent inside
this file's `BEGIN`/`COMMIT`. Same argument 013 records for
`answer_block_type`.

**The state machine is enforced by CHECK constraints, not only in Python.** A
job row is written by a worker process that can be killed at any instant; an
invariant living only in application code holds only when that code got to
finish. Hence: terminal status ⟺ `finished_at IS NOT NULL`; nothing finishes
without having started; a `failed` row must carry an `error`; a `queued` row
carries no `result`; `attempts >= 0`.

**`attempts` increments on the claim, not the enqueue,** so a job that is
repeatedly picked up and dies mid-run is distinguishable from one no worker
ever touched. The retry cap is worker policy, not a constraint.

**`result` is the run report, not the scores.** Scores are appended to
`evaluation_results`, the ledger of record (rule 2). Duplicating them here
would create a second, diverging record of what a student was given.

**`payload.blob_url` is a stable `"bucket/key"` ref, never a presigned URL**
(rule 1 / §10) — a queued job may not run for minutes, by which time a signed
URL would have expired.

**Indexes.** `idx_evaluation_jobs_claim` is partial on `WHERE status='queued'`:
the claim query runs on every poll of every worker, and succeeded jobs
accumulate forever, so it must not pay for them.
`idx_evaluation_jobs_college_created` serves the tenant's "my uploads" list.

## Still open

**RLS on this table is inert under a superuser.** Postgres exempts `SUPERUSER`
and `BYPASSRLS` roles from row-level security, and `FORCE ROW LEVEL SECURITY`
does not change that. With `PGUSER=postgres` (the `.env.example` default) the
policies above — and 003's — do nothing. `api/routers/jobs.py` therefore also
filters `college_id` explicitly in its `WHERE` clause, and that predicate is
currently the *only* thing isolating tenants. Fix is a non-superuser
application role; see `api/README.md`.

**Stalled jobs are not reaped automatically.** A worker killed between claiming
and finishing leaves the row `running` forever. `attempts` and `started_at` are
already on the row so a reaper has what it needs, but a correct one needs a
heartbeat/lease column — otherwise it races a slow-but-healthy job and runs it
twice. Until then, `scripts/run_job_worker.py --requeue-stalled MINUTES` is the
manual lever.

---

# Booklet uploads + job progress — migration notes

**File:** `015_booklet_uploads.sql`. Run after 001–014. Idempotent
(`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, guarded
`ADD CONSTRAINT`, `CREATE INDEX IF NOT EXISTS`, `DROP POLICY IF EXISTS` before
each `CREATE POLICY`), wrapped in `BEGIN`/`COMMIT`. Additive — one new table
plus one nullable column; no existing row is modified.

## What this adds

**`booklet_uploads`** — one uploaded answer-booklet PDF.

| column | type | notes |
|---|---|---|
| `upload_id` | UUID PK | `gen_random_uuid()` |
| `college_id` | UUID **NOT NULL** → `colleges` | many uploads → one college, `ON DELETE RESTRICT` |
| `blob_url` | TEXT NOT NULL | stable `"bucket/key"`, never a presigned URL |
| `filename` | TEXT NOT NULL | as the client sent it |
| `content_type` | TEXT NULL | client-declared, recorded for provenance only |
| `size_bytes` | BIGINT NOT NULL | `CHECK >= 0` |
| `storage_mode` | TEXT NOT NULL | `CHECK IN ('dummy','minio')` |
| `uploaded_at` | TIMESTAMPTZ NOT NULL | `now()` |

Plus the 003/014 RLS policy pair, an index on `(college_id, uploaded_at DESC)`,
and one on `blob_url` (looked up by value — see below).

**`evaluation_jobs.progress JSONB NULL`** — `{stage, percent, message, counts}`.

## Why booklet_uploads exists

Until 015, `POST /api/v1/upload` enqueued an `evaluation_jobs` row and returned
its id as `upload_id`, because one upload meant exactly one job.
`POST /api/v1/evaluate` ends that: it takes `{upload_id, exam_id, student_id}`,
so **one upload can be evaluated many times** — a different exam/student
binding, a re-run after a reference answer is corrected, a re-run after
re-ingestion.

With the old shape the upload's own job row becomes a job nobody handles: it
sits `queued` forever while `GET /api/v1/jobs/{id}` reports *"waiting for a
worker to pick this up"* about work no worker will ever pick up. An API that
lies about its own state is worse than one that needs another table.

**ONE `booklet_uploads` : MANY `evaluation_jobs`.**

**No FK from `evaluation_jobs` to here, deliberately.** 014 made `payload`
generic precisely so a new job kind needs no migration, and a typed
`upload_id` column would serve exactly one `job_type`. The cost is real and
stated rather than hidden — an `upload_id` inside a payload can dangle if its
upload is deleted. Mitigation is at the edge: `POST /api/v1/evaluate` resolves
the upload **tenant-scoped, before enqueueing**, so a job is never created
against an upload that does not exist, and `tests/test_api/test_evaluation.py`
asserts the 404.

**`blob_url` is indexed because it is the join key to the answers.**
There is no booklets table and no `exams.paper_id` (§7C's open gap), so
`core/booklet_evaluator.load_booklet_tasks` addresses a booklet as "the answers
one student wrote for one exam", optionally narrowed by `source_scan_url`.
`booklet_uploads.blob_url` is that `source_scan_url` — which is what makes
`{upload_id, exam_id, student_id}` a well-formed request rather than three
loosely related ids.

## Why progress is its own column

014 gave a job `payload` (input) and `result` (output, terminal only). A
running job had nothing to say between the two, so `GET /jobs/{id}` could only
report the four statuses — and "running" for four minutes with no further
signal is indistinguishable from "hung".

**Not stored in `payload`:** payload is the job's immutable input, and a worker
that overwrites its own input destroys the record of what it was asked to do,
and with it any chance of a faithful retry.

**`percent` is a stage marker, not a measured fraction.**
`core/booklet_evaluator` runs extraction and evaluation as bounded thread pools
with no progress callback, so completion *within* a phase is genuinely unknown.
Reporting which phase a job is in is honest; interpolating a percentage inside
one would be a fabricated number that a client renders as a smoothly moving
bar — and it would hide, from us, that real progress reporting does not exist.
Adding a callback to `core/booklet_evaluator.py` is what would make these real.

`api/schemas/jobs.py` ignores stored progress for any non-running job, so a job
that died during `persisting` does not keep reporting 85% forever.

## Still open

Same two gaps 014 records, unchanged: **RLS is inert under a superuser**
(the explicit `college_id` predicates in `core/uploads.py` and
`core/jobs.py` are what actually isolate tenants), and **there is no
stalled-job reaper**.

New: **the API cannot ingest.** An uploaded PDF is stored but never segmented
into `answer_blocks` rows — that is still `scripts/ingest_booklet.py`'s job
(§7C). `POST /api/v1/evaluate` therefore evaluates regions that must already
exist, and fails loudly naming the missing step if they do not.

---

# The application role — migration notes

`016_application_role.sql`. Creates no tables. It creates the database role
that makes every policy written in 003, 014 and 015 actually execute.

## What was wrong

Postgres exempts `SUPERUSER` and `BYPASSRLS` roles from row-level security.
`FORCE ROW LEVEL SECURITY` — which 003 applies, and whose comment explains it
— closes the table-**owner** loophole, not that one. With `PGUSER=postgres`
(the `.env.example` default until now, so every checkout) all nine
`tenant_isolation` policies were inert: two different values of
`app.current_college_id` returned identical rows, silently, with nothing in the
log. Three migrations' worth of isolation had never run once.

## What it creates

One login role, `ai_eval_app` by default (`APP_DB_USER` overrides):

* `NOSUPERUSER NOBYPASSRLS` — the entire point.
* **Owns nothing.** Every table stays owned by the migration runner. An owner
  can `ALTER TABLE ... NO FORCE ROW LEVEL SECURITY`, `DISABLE` it, or `DROP`
  the policies — the application must not be able to switch off its own
  isolation, and the migration RAISEs if the role owns any object in `public`.
* **DML grants only**, plus `ALTER DEFAULT PRIVILEGES` so migration 017's
  tables are reachable without anyone remembering. Deliberately withheld (and
  actively `REVOKE`d, so a hand-made role is corrected on re-run): `TRUNCATE`
  — which is **not** filtered by RLS, so one statement would empty every
  tenant at once — plus `REFERENCES`, `TRIGGER`, `CREATE` on the schema, and
  ownership.

**The password comes from `APP_DB_PASSWORD` in the environment**, read with
psql's `\getenv` before the transaction opens. A literal in this file would be
a committed credential, and re-running the migration would silently reset the
password back to it. `set_config(..., is_local => true)` holds it for the
transaction only, and the statement that loads it selects `IS NOT NULL` rather
than the value, so the password is not echoed to the operator's terminal or
into a teed log.

The closing assertions refuse to commit a role that still bypasses RLS, a role
that owns anything, or a schema where one of the nine tenant tables is missing
`ENABLE`/`FORCE ROW LEVEL SECURITY`.

## What it changes elsewhere

* **`.env.example`** points `PGUSER` at the app role and adds
  `PGADMIN_USER`/`PGADMIN_PASSWORD` for owner work.
* **`scripts/reset_and_seed_db.sh`** runs `pg_dump`, `TRUNCATE` and the seed as
  the admin user. Not a convenience: the app role has no `TRUNCATE` grant, and
  a `pg_dump` taken as it would **succeed** and produce a backup with zero rows
  in all nine RLS-protected tables — no tenant context is set during a dump,
  and RLS fails closed silently. A restorable-looking empty backup is the worst
  outcome this script has available.
* **`seed_minimal.sql`** seeds a second REAL college (with its own student and
  exam) and a suspended one, because `api/deps/db.py::get_tenant_conn` now
  refuses an unknown or inactive tenant with 401 — so "the other tenant" in a
  cross-tenant test has to be a college that actually exists, or the test
  proves only that an unknown caller is rejected.
* **Two tests stopped skipping.**
  `test_rls_isolation.py::test_rls_policies_isolate_tenants` and
  `test_tenant_context.py::test_tenant_connection_is_actually_rls_scoped` now
  FAIL under a bypassing role instead of skipping: since the fix exists, a
  superuser `PGUSER` is a misconfiguration, and a skip would report green while
  the property is untested.

## Still open

**The explicit `college_id` predicates are not redundant now.** They were the
only isolation before 016 and they are the only isolation the moment anyone
points `PGUSER` back at a superuser — one `.env` edit, no visible symptom. The
tests that pin them (`test_the_isolating_predicate_is_not_only_rls`,
`test_isolation_holds_at_the_query_layer_not_only_via_rls`) now drive the query
on a **platform-admin** connection, where the permissive bypass policy makes
every tenant's rows visible, so that the predicate is provably the thing
refusing the row.

**Roles are cluster-wide; grants are per-database.** Running 016 against a
second database in the same cluster re-uses the role and issues that database's
grants.

**No stalled-job reaper** (unchanged, see 014).


---

# Migration 017 — `users` + `refresh_tokens` (authentication identity)

Applied 2026-09-06. The migration file's own header carries the full argument;
this is the summary and the parts a reader of THIS file needs.

## `reviewers` is not modified. Not one column, not one policy.

That is the whole design decision. `reviewers` is the ACTOR — shared between
the answer and question schemas, referenced by six FKs, nullable `college_id`,
no RLS. `users` is the CREDENTIAL + TENANCY for those actors who can log in,
1:0..1 with `reviewers` via a `UNIQUE NOT NULL reviewer_id`, so there is
exactly one login per human and it is still `reviewer_id` that appears in every
provenance row.

Putting the auth columns on `reviewers` was considered and rejected for four
reasons; the one that decided it is worth repeating here because it is a
security failure rather than a modelling preference:

> `reviewers.college_id IS NULL` already means "platform-level reviewer", and
> it is the PERMISSIVE branch of `fn_derive_and_check_answer_review_college()`
> (003 §6). A `password_hash` column obliges us to put RLS on the table. The
> moment we do, that trigger's `SELECT college_id FROM reviewers` becomes
> policy-filtered, a College-B reviewer's row is invisible from a College-A
> session, the lookup returns NULL, and **the cross-college review check
> passes** — silently, in the direction that reads as "allowed". There is no
> configuration of `reviewers` that both protects hashes and keeps that
> trigger honest.

§7's assertions fail the migration if `reviewers` ever acquires RLS, so that
cannot be re-introduced by accident.

## The login problem, and why it is not solved with a bypass

Authentication happens BEFORE any tenant context exists — resolving the college
is the OUTPUT of the login, not an input to it. Both obvious answers are wrong:
leaving `users` unprotected puts password hashes in the one table with no
isolation, and setting `app.is_platform_admin` for the login query flips the
permissive bypass policy on all ELEVEN protected tables for the whole
transaction, giving an UNAUTHENTICATED request cross-tenant read on every
answer in the platform.

Instead the login read is a **single-row window, opened two ways at once**:

* **row access** — the `auth_lookup` policy matches exactly the row whose email
  equals `app.auth_lookup_email`. Unset GUC ⇒ NULL ⇒ matches nothing.
* **column access** — §4 REVOKEs table-wide SELECT from the application role
  and re-grants it column by column, WITHOUT `password_hash`. The hash is not
  readable by the application at all; it is only ever a return value of
  `auth_lookup_user()`, which is SECURITY DEFINER and sets/restores the GUC
  around one statement.

`refresh_tokens` goes further: the application role has **no direct privilege
on it whatsoever**, so the `auth_session_ops` policy is only ever traversed
from inside the definer functions. A stray `SET app.auth_token_hash` in
application code buys nothing, because the privilege check fails too.

The five entry points (`auth_lookup_user`, `auth_user_by_id`,
`auth_issue_refresh_token`, `auth_redeem_refresh_token`,
`auth_revoke_refresh_token`, `auth_revoke_all_refresh_tokens`) each set their
GUC transaction-locally, run ONE statement against ONE row (the last is bounded
to one user), and restore it. `EXECUTE` is revoked from PUBLIC first — Postgres
grants it by default, which on a SECURITY DEFINER function means every role in
the cluster.

## Why `auth_redeem_refresh_token` tests liveness itself

`revoked_at IS NULL AND expires_at > now()` lives inside the function rather
than in any caller's WHERE clause, so no call site can forget it. Forgetting it
once would make logout cosmetic, which is the entire reason the table exists.

## Assertions (§7)

The migration fails if: RLS is not ENABLE+FORCE on both tables; the application
role can SELECT `users.password_hash`; it cannot SELECT `users.email` (i.e. §4
revoked more than it re-granted); it has direct SELECT on `refresh_tokens`;
`auth_user_by_id` returns a `password_hash`; or `reviewers` has acquired RLS.

## Still open

* **No admin-invite endpoint.** Accounts other than the first are created by
  `core/users.py::create_user`, called from a script. The first platform admin
  comes from `scripts/bootstrap_platform_admin.py`.
* **No password-change endpoint.** When one is written it must call
  `auth_revoke_all_refresh_tokens()` in the same transaction.
* **`reviewers.email` and `users.email` both exist and are not synced**, on
  purpose: `users.email` is the citext UNIQUE credential, `reviewers.email` is
  the display/contact field it has always been, and syncing them would make a
  login rename rewrite a row six FKs point at.
