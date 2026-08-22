# AI Exam Evaluation Platform — project context

Use this file to onboard a new Claude session. It is the single source of
truth for what has been built, what decisions were made, and what is next.

---

## What this system is

An AI-assisted exam platform sold to colleges (multi-tenant). Three components:
1. Question Knowledge Repository — question bank linked to source content,
   topics, keywords, reference answers.
2. Question Generation — teachers upload content; AI generates questions;
   mandatory human review before a question goes live.
3. Answer Evaluation — student scans → OCR → AI semantic matching → SME
   review → finalization → LMS/reports.

## Tech stack

- PostgreSQL 16 — relational core. JSONB for variable-shaped data, recursive
  CTEs for hierarchies.
- pgvector — already provisioned, not yet used. Intended for semantic
  similarity in answer evaluation and future RAG-based question generation.
- MinIO (S3-compatible) — binary blob storage (scans, diagrams). DB stores
  only URLs, never blobs. Currently in "dummy" mode (placeholder URLs).
- Redis — job queues and review locks. Not yet implemented.
- Python 3.10+ — all scripts.
- LLM: Groq API (free tier, llama-3.3-70b-versatile). Provider is pluggable
  via LLM_PROVIDER env var (ollama/groq/gemini supported in core/llm.py).

## Repository structure

```
ai-exam-evaluation/
├── migrations/
│   ├── 001_answer_schema.sql
│   ├── 002_question_schema.sql
│   ├── 003_multi_tenancy.sql
│   ├── 004_add_question_text.sql
│   └── 005_question_tree_and_ai_flag.sql
├── core/
│   ├── __init__.py
│   ├── db.py          # psycopg2 connection, reads PG* env vars
│   ├── storage.py     # dummy/minio blob upload, presigned URL helper
│   └── llm.py         # pluggable LLM provider (groq default)
├── scripts/
│   ├── extract_exam_bank.py   # docx → structured JSON + media (uses pandoc)
│   ├── load_exam_bank.py      # JSON → Postgres + MinIO (idempotent, uuid5 IDs)
│   └── reword_question.py     # LLM reword → new draft question (derivation tree)
├── .env.example
├── requirements.txt   # psycopg2-binary only; boto3 optional for minio mode
└── PROJECT_CONTEXT.md
```

NOTE: db.py and storage.py also exist at the repo root (legacy location
before the core/ restructure). The scripts/ folder now imports from core/.

---

## Database schema — 20 tables + 1 view + 1 tenant table

### Question schema (12 tables) — SHARED across all colleges, no college_id

- paragraphs(paragraph_id PK, content, source_document, version, status[active|superseded], uploaded_at)
- sentences(sentence_id PK, paragraph_id FK, content, sequence_order, is_question_worthy BOOL nullable)
- topics(topic_id PK, name, parent_topic_id FK→topics nullable — TREE, not DAG)
- topic_links(link_id PK, entity_type[paragraph|question], entity_id UUID polymorphic, topic_id FK)
- questions(question_id PK, source_type[sentence|paragraph|diagram|table|formula|manual],
    source_id UUID nullable, style[long|short|one_word|mcq], marks_max REAL,
    status[draft|confirmed|rejected|live|superseded], supersedes_question_id FK→questions nullable,
    parent_question_id FK→questions nullable,   ← ADDED in 005: derivation tree
    is_ai_generated BOOL nullable,              ← ADDED in 005: human vs AI flag
    question_group_id UUID, created_at, content TEXT)
- keywords(keyword_id PK, term UNIQUE)
- question_keywords(question_id FK, keyword_id FK, weight REAL — composite PK)
- content_assets(asset_id PK, asset_type[diagram|table|formula], blob_url nullable, structured_data JSON nullable, uploaded_at)
- question_asset_links(link_id PK, asset_id FK, role[question_source|answer_component],
    question_id FK nullable, reference_answer_variant_id FK nullable)
- reference_answer_variants(variant_id PK, question_id FK, variant_type[short|long],
    content, is_current BOOL, created_at — APPEND-ONLY, never overwrite)
- question_reviews(review_id PK, question_id FK, reviewer_id FK, action[confirmed|rejected], comment, reviewed_at)
- question_status_history(history_id PK, question_id FK, old_status TEXT, new_status TEXT,
    changed_by FK→reviewers nullable, changed_at)

### Answer schema (8 tables) — SINGLE-TENANT, every table has college_id + RLS

- colleges(college_id PK, name, short_code UNIQUE, status[active|suspended], created_at)  ← tenant table
- students(student_id PK, college_id FK, name, roll_number, email, enrolled_at
    — UNIQUE(college_id, roll_number))
- exams(exam_id PK, college_id FK, name, conducted_at, status[draft|live|closed])
- answers(answer_id PK, college_id FK [derived by trigger from student], question_id FK→questions,
    student_id FK, exam_id FK, source_scan_url, text_extracted, status[pending_evaluation|
    ai_scored|sme_reviewed|finalized|flagged], submitted_at)
- answer_blocks(block_id PK, college_id FK [derived], answer_id FK, block_type[text|diagram|table|formula],
    blob_url nullable, content nullable, sequence_order INT, confidence_score REAL nullable)
- reviewers(reviewer_id PK, college_id FK nullable — NULL=platform-level SME, name, role[teacher|sme|admin], email)
- evaluation_results(evaluation_id PK, college_id FK [derived], answer_id FK [NOT unique — append-only],
    reference_answer_variant_id FK, evaluator_type[ai|sme], score REAL, explanation,
    is_current BOOL, evaluated_at)
- answer_reviews(review_id PK, college_id FK [derived], answer_id FK, reviewer_id FK,
    action[confirmed|overridden|flagged], final_marks REAL nullable, comment, reviewed_at)
- answer_status_history(history_id PK, college_id FK [derived], answer_id FK, old_status TEXT,
    new_status TEXT, changed_by FK→reviewers nullable, changed_at)

### View (added in 005)
- question_tree — recursive CTE view. Given a root_question_id, returns
  every derived question with depth and ancestry path array. Used to render
  the derivation tree in the UI.

---

## Key design patterns (non-negotiable)

- NEVER store blobs in Postgres — MinIO + URL only.
- NEVER overwrite scores or reference answer variants — append new row,
  flip is_current. Same for evaluation_results.
- source_scan_url on answers is the untouched original — never overwritten
  even if text_extracted is later corrected.
- Prefer one generalized table over per-type duplicates (topic_links,
  content_assets — already corrected once).
- college_id on answer-schema tables is ALWAYS derived by trigger from the
  parent row, never trusted from app input.
- RLS is enabled and FORCED on all 7 answer-schema tables. App must set
  `SET LOCAL app.current_college_id = '<uuid>'` per transaction.

---

## Cross-schema integrity triggers (in 001_answer_schema.sql)

1. trg_answers_question_must_be_live — blocks creating an answer against a
   question whose status != 'live'.
2. trg_evaluation_variant_matches_answer_question — blocks linking an
   evaluation_result to a reference_answer_variant that belongs to a
   different question than the answer being evaluated.

---

## question_group_id vs parent_question_id vs supersedes_question_id

These three serve distinct purposes — do NOT conflate them:

- question_group_id: groups ALL versions of "the same question" for
  reporting. Aggregate queries must group by this, not question_id,
  otherwise answers against old versions are silently excluded.

- supersedes_question_id: strict replacement chain. Original retires
  (status→superseded), new version takes over. Used when correcting a
  factual error in a live question.

- parent_question_id: derivation tree (added 005). Original stays active.
  New question is a creative branch (reword, difficulty variant). Multiple
  children can share one parent. Traversed via question_tree view.

---

## is_ai_generated vs source_type

- source_type = WHERE the question came from (what source material).
  paragraph/sentence/diagram/table/formula = type of source; manual = no
  source. Does NOT indicate who or what created the question text.

- is_ai_generated = HOW the question text was written.
  TRUE = LLM generated the content. FALSE/NULL = human wrote it.
  NULL means unknown/legacy (pre-005 questions).

---

## LLM integration (core/llm.py)

Provider chosen by LLM_PROVIDER env var, default = groq.

Supported: ollama (local, free, no signup), groq (free tier, GROQ_API_KEY),
gemini (free tier, GEMINI_API_KEY). Anthropic removed — paid, not in use.

All providers use stdlib urllib — no extra dependencies. Adding a new
provider = one function (~6 lines) + one entry in _PROVIDERS dict.
Nothing else in the codebase changes.

Current issue: Groq API is fronted by Cloudflare. Fixed by adding
User-Agent: exam-platform-backend/1.0 header in _post_json(). Without it,
Cloudflare returns HTTP 403 error code 1010.

---

## Scripts

### extract_exam_bank.py
Converts a .docx question bank file to structured JSON + extracts embedded
images. Uses pandoc JSON AST (not python-docx — pandoc handles OMML math
conversion). Output: extracted/exam_bank.json + extracted/media/*.

### load_exam_bank.py
Loads extracted JSON into Postgres. Idempotent (uuid5 deterministic IDs,
ON CONFLICT DO UPDATE). Storage modes: dummy (default, no MinIO needed —
writes placeholder blob_url string) or minio (real upload).
Only populates Question schema tables. exam_bank.docx is a question bank,
not student submissions — answers/students/exams untouched.

### reword_question.py
Rewording behaviour: original question is NEVER superseded. New draft
question inserted with parent_question_id = original (tree edge).
is_ai_generated = True on the new question. Metadata carried forward:
question_keywords, topic_links, question_asset_links(role=question_source).
NOT carried forward: reference_answer_variants, answer rows.
--stub-llm flag: skips real LLM call for testing.
--show-tree flag: prints derivation tree after rewording.
--dry-run flag: rolls back all DB writes.

---

## What is complete

- All 5 migrations written and verified against real Postgres 16.
- Question bank (exam_bank.docx — 10 CS questions) loaded into DB.
- LLM integration working with Groq API.
- Question rewording working end-to-end with derivation tree.
- Multi-tenancy: shared question schema, single-tenant answer schema with
  RLS, college_id derivation triggers.

## What is next (boss's task list)

### Task 1 — Generate questions from content (experiment)
Decided: teachers upload content → lands in paragraphs table → AI generates
questions from it → teacher approves → goes into real question bank as draft.
Three approaches to compare: (a) internet search, (b) RAG using pgvector on
paragraphs corpus, (c) intent-hinted (teacher specifies focus/difficulty).
Open: who owns the RAG/model work (backend vs ML team)?
Open: success metric for comparing approaches.

### Task 2a/2b — Paper pattern schema
Decided: optional-choice count set per-paper-generation, not baked into
pattern. Patterns shared across colleges (no college_id on patterns).
Pattern creator: nullable created_by (NULL = system template, reviewer_id =
teacher-owned). Not yet designed or migrated.

### Task 2d — Derive pattern from questions
No design questions open. Pure aggregation: given question_ids, group by
marks_max + style, count each bucket. No new tables. Can build now.

### Task 2e — Answer preview
Decided: full preview — scanned image (presigned URL from storage.py) +
answer_blocks (type, content, confidence_score) + text_extracted.
Low-confidence blocks should be flagged. Not yet built.

---

## Environment variables (.env)

PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD — Postgres connection.
LLM_PROVIDER=groq — LLM provider.
GROQ_API_KEY=gsk_... — Groq API key (free tier, console.groq.com).
MINIO_* — only needed if --storage minio (currently using dummy mode).

Load with: set -a && source .env && set +a
