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

- **PostgreSQL** — relational core with JSONB for variable-shaped fields, recursive CTEs for topic trees
- **pgvector** — Postgres extension for semantic similarity in AI evaluation
- **MinIO** (S3-compatible) — object storage for binary files (scans, diagram images); the database stores only URL references, never blobs
- **Redis** — job queues (OCR/evaluation jobs) and review locks
- **Python** — backend scripts and services
- **psycopg2** — Postgres driver

## Project Structure

```
.
├── migrations/                # Versioned SQL migrations
│   ├── 001_answer_schema.sql
│   ├── 002_question_schema.sql
│   ├── 003_multi_tenancy.sql
│   ├── 004_add_question_text.sql
│   └── README.md              # Migration guide and schema docs
├── scripts/
│   ├── extract_exam_bank.py   # Parses exam_bank.docx → structured JSON + images
│   └── load_exam_bank.py      # Loads extracted JSON into Postgres (+ optional MinIO)
├── readme files/              # Design decision docs
│   ├── answer-schema-design.md
│   └── question-schema-design.md
├── db.py                      # Postgres connection helper (reads PG* env vars)
├── storage.py                 # Object storage abstraction (dummy / MinIO modes)
├── requirements.txt
├── PROJECT_CONTEXT.md         # Authoritative design doc — read before contributing
└── .gitignore
```

## Database Schema

The database is split into two schemas, connected by shared foreign keys:

**Question schema (12 tables):** `paragraphs`, `sentences`, `topics`, `topic_links`, `questions`, `keywords`, `question_keywords`, `content_assets`, `question_asset_links`, `reference_answer_variants`, `question_reviews`, `question_status_history`

**Answer schema (8 tables):** `students`, `exams`, `answers`, `answer_blocks`, `reviewers` *(shared)*, `evaluation_results`, `answer_reviews`, `answer_status_history`

See [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) for full column-level definitions, cross-schema integrity rules, and open design decisions.

Migration files with detailed comments live in [`migrations/`](migrations/).

## Getting Started

### Prerequisites

- Python 3.10+
- PostgreSQL 14+ (with `pgvector` extension for AI evaluation features)
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

# 5. Run migrations in order
psql -f migrations/001_answer_schema.sql
psql -f migrations/002_question_schema.sql
psql -f migrations/003_multi_tenancy.sql
psql -f migrations/004_add_question_text.sql
```

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

See [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) §8 for the full list of non-negotiable patterns.

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
