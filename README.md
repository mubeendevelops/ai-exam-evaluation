# AI Exam Evaluation Platform

An AI-assisted platform for managing question banks, generating exam questions from source content, and evaluating student answers through a combination of AI scoring and human expert review.

## Overview

The platform consists of three core components:

| Component | Purpose |
|---|---|
| **Question Knowledge Repository** | A question bank where each question is linked to its source content, topics, keywords, and reference answers. |
| **Question Generation** | Teachers define a schema (marks distribution, topic coverage, style) and questions are generated from uploaded content (paragraphs, diagrams, tables, formulae) with mandatory human review before going live. |
| **Answer Evaluation** | Scanned/uploaded student answers go through: digitization → AI semantic matching → SME review/override → finalization → delivery to LMS/reports. |

## Tech Stack

- **PostgreSQL** — relational core with JSONB for variable-shaped fields, recursive CTEs for topic trees; also the job queue (`evaluation_jobs`, claimed with `FOR UPDATE SKIP LOCKED`) — **Redis was considered and explicitly rejected** for that role, see `migrations/README.md`
- **MinIO** (S3-compatible) — object storage for binary files (scans, diagram images); the database stores only URL references, never blobs
- **Python** — backend scripts, a FastAPI layer, and a worker process
- **psycopg2** — Postgres driver
- **sentence-transformers** — embeddings-based semantic scoring (no pgvector: nothing here is persisted as a vector column; scores are computed on the fly)

## Project Structure

```
.
├── migrations/                # Versioned SQL migrations, 001 through 019 (see migrations/README.md)
├── core/                      # Shared library imported by every script AND the API
│   ├── db.py                  # Postgres connection helper (reads PG* env vars)
│   ├── storage.py              # Object storage abstraction (dummy / MinIO modes)
│   └── ...                    # evaluator, diagram/table/booklet pipelines, auth, etc.
├── api/                       # FastAPI layer over core/ — see api/README.md
├── frontend/                  # Vite + React + TypeScript UI
├── scripts/                   # 28 CLI entry points, e.g.:
│   ├── extract_exam_bank.py   # Parses exam_bank.docx → structured JSON + images
│   └── load_exam_bank.py      # Loads extracted JSON into Postgres (+ optional MinIO)
├── tests/                     # pytest suite (`make test`)
├── readme files/               # Design decision docs
│   ├── PROJECT_CONTEXT.md     # Authoritative design doc — read before contributing
│   ├── SCRIPT_COMMANDS.md     # Verified CLI command reference
│   ├── answer-schema-design.md
│   └── question-schema-design.md
├── requirements.txt
└── .gitignore
```

This is a directory-level overview only — see [`CLAUDE_CONTEXT.md`](CLAUDE_CONTEXT.md) §3 for the full, current file tree.

## Database Schema

The database is split into two schemas, connected by shared foreign keys:

**Question schema (12 tables):** `paragraphs`, `sentences`, `topics`, `topic_links`, `questions`, `keywords`, `question_keywords`, `content_assets`, `question_asset_links`, `reference_answer_variants`, `question_reviews`, `question_status_history`

**Answer schema (12 tables, 9 RLS-protected single-tenant):** `students`, `exams`, `answers`, `answer_blocks`, `evaluation_results`, `answer_reviews`, `answer_status_history`, `evaluation_jobs`, `booklet_uploads` (all RLS-protected); `colleges`, `reviewers` *(shared)*, `glossary_terms` *(shared, no `college_id`)* (not tenanted). A separate `users`/`refresh_tokens` pair (migration 017) backs authentication.

See [`readme files/PROJECT_CONTEXT.md`](readme%20files/PROJECT_CONTEXT.md) for full column-level definitions, cross-schema integrity rules, and open design decisions; see [`CLAUDE_CONTEXT.md`](CLAUDE_CONTEXT.md) for the maintained, up-to-date repo snapshot (this README covers only the original question-bank slice).

Migration files with detailed comments live in [`migrations/`](migrations/).

## Getting Started

### Prerequisites

- Python 3.10+
- PostgreSQL 14+
- [pandoc](https://pandoc.org/installing.html) (only needed if re-extracting from `.docx` source files)

### Setup

```bash
# 1. Clone the repo
git clone https://github.com/<your-org>/ai-evaluation-system.git
cd ai-evaluation-system

# 2. Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure Postgres connection via environment variables
export PGHOST=localhost
export PGPORT=5432
export PGDATABASE=ai_evaluation
export PGUSER=postgres
export PGPASSWORD=your_password

# 5. Apply migrations (001 through 019) — scripts/migrate.py is the canonical
#    runner: it applies files in order and records what it applied, so
#    --status can answer "is this DB current?" without guessing.
python3 scripts/migrate.py --up
```

See [`CLAUDE_CONTEXT.md`](CLAUDE_CONTEXT.md) §8 for the full setup sequence, including bootstrapping the first login and baselining a database that already has some migrations applied by hand.

### Loading the Question Bank

The question bank pipeline has two stages:

```
exam_bank.docx
     │  extract_exam_bank.py   (pandoc → structured JSON + image files)
     ▼
extracted/exam_bank.json + extracted/media/…
     │  load_exam_bank.py      (uploads images to MinIO, writes rows to Postgres)
     ▼
Postgres (questions, reference_answer_variants, content_assets, …) + MinIO (diagram blobs)
```

```bash
# Extract (requires pandoc)
python3 scripts/extract_exam_bank.py "exam bank.docx" --outdir ./extracted

# Preview what will be loaded (dry run)
python3 scripts/load_exam_bank.py extracted/exam_bank.json --dry-run

# Load into Postgres (dummy storage mode — no MinIO needed)
python3 scripts/load_exam_bank.py extracted/exam_bank.json --status draft --storage dummy
```

The loader is **idempotent** — all IDs are deterministic (`uuid5`), so re-running against the same data updates existing rows via `ON CONFLICT … DO UPDATE` instead of creating duplicates.

### Switching to Real Object Storage (MinIO)

```bash
pip install boto3
export MINIO_ENDPOINT=http://localhost:9000
export MINIO_ACCESS_KEY=minioadmin
export MINIO_SECRET_KEY=minioadmin
export MINIO_BUCKET=exam-platform

python3 scripts/load_exam_bank.py extracted/exam_bank.json --status draft --storage minio
```

## Design Principles

- **No blobs in Postgres** — always object storage + URL reference
- **Append-only audit trail** — never overwrite a score or reference answer; insert new row, flip `is_current`
- **Generalized tables over duplication** — one `topic_links` table, one `content_assets` table, not per-type copies
- **Explicit nullability and cardinality** — every FK states whether it can be null and its cardinality

See [`readme files/PROJECT_CONTEXT.md`](readme%20files/PROJECT_CONTEXT.md) §8 for the full list of non-negotiable patterns.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `PGHOST` | Yes | Postgres host (default: `localhost`) |
| `PGPORT` | Yes | Postgres port (default: `5432`) |
| `PGDATABASE` | Yes | Database name (default: `ai_evaluation`) |
| `PGUSER` | Yes | Postgres user (default: `postgres`) |
| `PGPASSWORD` | Yes | Postgres password |
| `MINIO_ENDPOINT` | Only with `--storage minio` | MinIO endpoint URL |
| `MINIO_ACCESS_KEY` | Only with `--storage minio` | MinIO access key |
| `MINIO_SECRET_KEY` | Only with `--storage minio` | MinIO secret key |
| `MINIO_BUCKET` | Only with `--storage minio` | MinIO bucket name |

## License

Internal use only. Not licensed for external distribution.
