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
