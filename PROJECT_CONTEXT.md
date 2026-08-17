# Project context — AI Exam Evaluation Platform

Read this fully before writing any code. It is the single source of truth for
what this system is, what's already decided, and what's deliberately still open.

---

## 1. What this system is

An AI-assisted platform with three components:

1. **Question Knowledge Repository** — a question bank where each question is
   linked to its source content, topics, keywords, and reference answers.
2. **Question Generation** — teachers define a schema (marks distribution,
   topic coverage, style), and questions are generated from uploaded content
   (paragraphs, diagrams, tables, formulae), with mandatory human review
   before a question goes live.
3. **Answer Evaluation** — scanned/uploaded student answers go through:
   digitization (scan → OCR → segmentation → storage) → AI semantic matching
   → SME review/override → finalization → delivery to LMS/reports.

## 2. My scope (backend + infra)

Own: database schema, digitization pipeline orchestration (not the OCR/
segmentation models themselves — the service wrapper around them), REST APIs
(individual delivery + bulk), infra (CI/CD, job queues, sandbox instances),
LMS/MIS reporting integration.

Not owned: OCR/segmentation AI models, semantic-matching AI, GraphRAG/
knowledge-graph reasoning. Consume these as services with a defined
input/output contract — do not implement the models.

## 3. Tech stack

- **PostgreSQL** — the relational core for both schemas below. Use JSONB for
  variable-shaped fields, recursive CTEs for the topic tree.
- **pgvector** (Postgres extension) — semantic similarity for AI evaluation.
  Do not stand up a separate vector DB unless scale demands it later.
- **MinIO** (S3-compatible object storage) — every binary file (scans,
  diagram images). The database stores only the URL, never the blob.
- **Redis** — job queues (OCR/evaluation jobs) and review locks (prevent two
  reviewers finalizing the same answer concurrently). Not for permanent data.
- **Python** (Django or FastAPI — not yet finalized, confirm before scaffolding),
  ReactJS/Angular frontend, Jenkins/Puppet for CI/CD.
- Do **not** use the Java/Spring/MySQL stack — that appeared in prior academic
  research only, not this system.
- **Neo4j/graph DB — do not add.** The topic hierarchy is a plain tree
  (`parent_topic_id` self-reference in Postgres), not a DAG. Revisit only if
  that decision is explicitly overturned (see open decisions, §6).

## 4. Question schema (12 tables)

```
paragraphs(paragraph_id PK, content, source_document, version, status[active|superseded], uploaded_at)

sentences(sentence_id PK, paragraph_id FK->paragraphs, content, sequence_order, is_question_worthy)

topics(topic_id PK, name, parent_topic_id FK->topics NULLABLE self-ref)

topic_links(link_id PK, entity_type[paragraph|question], entity_id, topic_id FK->topics)
  -- polymorphic tagging table; do not create separate paragraph_topics/question_topics tables

questions(question_id PK, source_type[sentence|paragraph|diagram|table|formula|manual],
  source_id NULLABLE, style[long|short|one_word|mcq], marks_max, status[draft|confirmed|rejected|live|superseded],
  supersedes_question_id FK->questions NULLABLE self-ref, question_group_id, created_at)
  -- source_id is NULLABLE: manually-authored questions have no source
  -- question_group_id: same value across all versions of "the same" question (for reporting/aggregation)

keywords(keyword_id PK, term UNIQUE)

question_keywords(question_id FK, keyword_id FK, weight)

content_assets(asset_id PK, asset_type[diagram|table|formula], blob_url NULLABLE, structured_data JSON NULLABLE, uploaded_at)
  -- one table for all three asset types; do not create separate diagrams/tables/formulae tables

question_asset_links(link_id PK, asset_id FK->content_assets, role[question_source|answer_component],
  question_id FK NULLABLE, reference_answer_variant_id FK NULLABLE)
  -- a diagram can be BOTH a question source and embedded in a reference answer — two roles, one asset table

reference_answer_variants(variant_id PK, question_id FK->questions, variant_type[short|long],
  content, is_current, created_at)
  -- append-only: never overwrite a row, insert new + flip old is_current to false

question_reviews(review_id PK, question_id FK, reviewer_id FK->reviewers, action[confirmed|rejected], comment, reviewed_at)

question_status_history(history_id PK, question_id FK, old_status, new_status, changed_by FK->reviewers NULLABLE, changed_at)
```

## 5. Answer schema (8 tables)

```
students(student_id PK, name, roll_number UNIQUE, email, enrolled_at)

exams(exam_id PK, name, conducted_at, status[draft|live|closed])

answers(answer_id PK, question_id FK->questions [external], student_id FK->students, exam_id FK->exams,
  source_scan_url, text_extracted, status[pending_evaluation|ai_scored|sme_reviewed|finalized|flagged], submitted_at)
  -- source_scan_url = untouched original, never overwritten, even if text_extracted is later corrected

answer_blocks(block_id PK, answer_id FK->answers, block_type[text|diagram|table|formula],
  blob_url NULLABLE, content NULLABLE, sequence_order, confidence_score NULLABLE)
  -- confidence_score drives low-confidence routing to manual review

reviewers(reviewer_id PK, name, role[teacher|sme|admin], email)
  -- SHARED table: also referenced by question_reviews and question_status_history above

evaluation_results(evaluation_id PK, answer_id FK->answers [NOT unique — many rows per answer over time],
  reference_answer_variant_id FK->reference_answer_variants [external], evaluator_type[ai|sme],
  score, explanation, is_current, evaluated_at)
  -- append-only score ledger; exactly one is_current=true row per answer at any time

answer_reviews(review_id PK, answer_id FK->answers, reviewer_id FK->reviewers,
  action[confirmed|overridden|flagged], final_marks NULLABLE, comment, reviewed_at)
  -- separate from evaluation_results: this is the human-action audit log, not the score itself

answer_status_history(history_id PK, answer_id FK->answers, old_status, new_status,
  changed_by FK->reviewers NULLABLE, changed_at)
```

## 6. Cross-schema connections

- `answers.question_id → questions.question_id`
- `evaluation_results.reference_answer_variant_id → reference_answer_variants.variant_id`
- `reviewers` is one shared table for both schemas

**Two integrity rules that plain foreign keys cannot enforce — build these as
application-layer checks or DB triggers, not assumptions:**
1. An `answers` row should only be creatable against a `questions` row with
   `status = live`. Nothing currently stops answering a draft question.
2. `evaluation_results.reference_answer_variant_id` must belong to the same
   `question_id` as the `answers` row it's evaluating. Two independently-valid
   FKs can currently point at mismatched questions.

**Reporting note:** because of question versioning (`supersedes_question_id`),
aggregate queries like "how many students answered this question" must group
by `question_group_id`, not filter by a single `question_id` — otherwise
answers against older versions are silently excluded.

## 7. Known open decisions — do not resolve unilaterally, confirm with product owner first

- **Topic hierarchy shape**: currently modeled as a tree (one parent per
  topic). If multi-parent topics are ever required, this needs a redesign
  (possibly a graph DB) — do not bolt on multi-parent support to the tree
  structure as a workaround.
- **Answer correction/versioning**: `answers.text_extracted` has no version
  history. If OCR-correction becomes a required workflow, this table needs a
  revision mechanism before that feature is built.
- **Student resubmission**: nothing currently allows more than one `answers`
  row per (student, question) pair. Confirm whether resubmission is in scope
  before assuming one answer per question is permanent.
- **Question deletion cascade**: recommended behavior is `RESTRICT` (a
  question with existing answers should not be hard-deletable; use
  `status = superseded`/soft-delete instead) — this is a recommendation, not
  yet a confirmed decision.
- **Re-opening a finalized answer**: the `status` enum on `answers` has no
  path back out of `finalized` for re-evaluation against an updated reference
  answer. Needs either a new status value or a redefinition of what
  `finalized` means.
- **OCR engine choice**: not yet selected (cloud API vs. specialized
  handwriting model). Build the digitization service behind a stable
  input/output contract so the engine can be swapped without touching
  downstream code.
- **exam_id optionality**: currently required on every `answers` row. No
  current path for practice/non-exam answers — confirm if that's in scope.

## 8. Non-negotiable design patterns already established

- Never store binary blobs in Postgres — always object storage + URL reference.
- Never overwrite a score or a reference answer variant — append a new row and
  flip `is_current`. This is the audit-trail mechanism for the whole system.
- Prefer one generalized table over duplicating the same shape per type (see
  `topic_links` and `content_assets` above) — this project has already hit
  and corrected this mistake once; don't reintroduce it elsewhere.
- State FK nullability and cardinality explicitly in every table — "can this
  be null," "is this unique" are not implementation details to leave
  implicit, they're the source of the bugs found during testing so far.
