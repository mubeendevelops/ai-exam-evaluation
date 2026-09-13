# AI Exam Evaluation Platform

An AI-assisted platform for managing question banks, generating exam questions from source content, and evaluating student answers through a combination of AI scoring and human expert review.

## Overview

The platform consists of three core components:

| Component | Purpose |
|---|---|
| **Question Knowledge Repository** | A question bank where each question is linked to its source content, topics, keywords, and reference answers. |
| **Question Generation** | Teachers define a schema (marks distribution, topic coverage, style) and questions are generated from uploaded content (paragraphs, diagrams, tables, formulae) with mandatory human review before going live. |
| **Answer Evaluation** | Scanned/uploaded student answers go through: digitization → AI semantic matching → SME review/override → finalization → delivery to LMS/reports. |

It ships as four pieces that run together: a **Postgres** database, a **FastAPI** backend (`api/`) plus a background **job worker**, a set of standalone **CLI scripts** (`scripts/`) that call the same shared `core/` library the API uses, and a **React/Vite** frontend (`frontend/`).

## Tech Stack

- **PostgreSQL 15** — relational core with JSONB for variable-shaped fields, recursive CTEs for topic trees; also the job queue (`evaluation_jobs`, claimed with `FOR UPDATE SKIP LOCKED`) — **Redis was considered and explicitly rejected** for that role, see `migrations/README.md`
- **MinIO** (S3-compatible) — object storage for binary files (scans, diagram images); the database stores only URL references, never blobs. A `dummy` storage mode exists so nothing but Postgres is required to develop against
- **FastAPI + uvicorn** — HTTP layer over `core/` (`api/`), JWT auth, per-tenant Postgres Row-Level Security
- **Python 3.10** — backend scripts, the FastAPI layer, and a worker process
- **psycopg2** — Postgres driver
- **sentence-transformers** — embeddings-based semantic scoring (no pgvector: nothing is persisted as a vector column; scores are computed on the fly)
- **PaddleOCR + Tesseract** — a multi-engine OCR fallback framework for reading handwritten diagram labels
- **Groq** — free-tier hosted LLM used for question generation, rewording, and one scoring signal
- **React 19 + TypeScript + Vite** — frontend (`frontend/`)

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
├── scripts/                   # CLI entry points, e.g.:
│   ├── migrate.py             # Canonical migration runner (--up / --status / --baseline)
│   ├── reset_and_seed_db.sh   # Backs up, truncates, and reseeds a disposable local DB
│   ├── bootstrap_platform_admin.py  # Creates the FIRST login (no open signup)
│   ├── run_job_worker.py      # Background worker that claims evaluation_jobs
│   ├── extract_exam_bank.py   # Parses exam_bank.docx → structured JSON + images
│   └── load_exam_bank.py      # Loads extracted JSON into Postgres (+ optional MinIO)
├── tests/                     # pytest suite (`make test`)
├── readme files/               # Design decision docs
│   ├── PROJECT_CONTEXT.md     # Authoritative design doc — read before contributing
│   ├── SCRIPT_COMMANDS.md     # Verified CLI command reference
│   ├── answer-schema-design.md
│   └── question-schema-design.md
├── docker-compose.yml         # Whole local stack: Postgres, MinIO, API, worker
├── docker-compose.minio.yml   # MinIO alone (also included by docker-compose.yml)
├── Dockerfile                 # Single image shared by the api and worker services
├── Makefile                   # make test / test-fast / test-cov
├── requirements.txt
└── .env.example               # Every environment variable, documented in place
```

This is a directory-level overview only — see [`CLAUDE_CONTEXT.md`](CLAUDE_CONTEXT.md) §3 for the full, current file tree.

## Database Schema

The database is split into two schemas, connected by shared foreign keys:

**Question schema (12 tables, shared across all tenants):** `paragraphs`, `sentences`, `topics`, `topic_links`, `questions`, `keywords`, `question_keywords`, `content_assets`, `question_asset_links`, `reference_answer_variants`, `question_reviews`, `question_status_history`

**Answer schema (12 tables, 9 RLS-protected single-tenant):** `students`, `exams`, `answers`, `answer_blocks`, `evaluation_results`, `answer_reviews`, `answer_status_history`, `evaluation_jobs`, `booklet_uploads` (all RLS-protected); `colleges`, `reviewers` *(shared)*, `glossary_terms` *(shared, no `college_id`)* (not tenanted). A separate `users`/`refresh_tokens` pair (migration 017) backs authentication.

See [`readme files/PROJECT_CONTEXT.md`](readme%20files/PROJECT_CONTEXT.md) for full column-level definitions, cross-schema integrity rules, and open design decisions; see [`CLAUDE_CONTEXT.md`](CLAUDE_CONTEXT.md) for the maintained, up-to-date repo snapshot.

Migration files with detailed comments live in [`migrations/`](migrations/).

---

## Getting Started

There are two ways to run this: **Docker** (fastest, gets the whole stack up including Postgres and MinIO) or a **bare-metal Python venv + local Postgres** (needed if you're doing OCR/diagram work, or don't want Docker). Both are documented below.

### Option A — Docker (recommended for a quick start)

#### Prerequisites

- Docker + Docker Compose v2 (`docker compose`, not `docker-compose`)

#### Steps

```bash
# 1. Clone the repo
git clone <repo-url>
cd "AI Evaluation System"

# 2. (Optional) copy env overrides — docker-compose.yml already has working
#    defaults for every value baked in, so this step can be skipped entirely
#    for a first run. Create .env only if you want to override something
#    (e.g. a real GROQ_API_KEY, or a non-default JWT_SECRET).
cp .env.example .env

# 3. Bring up Postgres and MinIO, and wait for Postgres to report healthy
docker compose up -d postgres minio

# 4. Load the minimal seed dataset (backs up nothing — this is a fresh DB —
#    runs migrations first, then truncates+reloads migrations/seed_minimal.sql)
docker compose run --rm seed

# 5. Bring up the API and worker (this also runs migrations automatically,
#    every time, as a dependency step — harmless once they're all applied)
docker compose up -d api worker
```

The API is now at **http://localhost:8000** (interactive docs at `/docs`), MinIO's S3 API at **http://localhost:9000** (console at **http://localhost:9001**, login `minioadmin` / `minioadmin123`), and Postgres at **localhost:5432**.

Create the first login (there is no open signup — every other account is created by an admin from inside the app):

```bash
docker compose run --rm api python scripts/bootstrap_platform_admin.py \
    --email admin@platform.example --name "Platform Admin"
```

Ordinary restarts afterward are just:

```bash
docker compose up -d
```

**Do not** run `docker compose run --rm seed` again on a stack you care about — it truncates every data table. It is intentionally *not* wired in as an automatic dependency of `up`, for that reason.

To run the frontend against this stack, see [Frontend setup](#frontend-setup) below.

### Option B — Bare-metal (Python venv + local Postgres)

Use this path if you need to work on OCR/diagram extraction, or don't want Docker.

#### Prerequisites

- **Python 3.10+**
- **PostgreSQL 14+** (15 recommended), running locally or reachable over the network
- **[pandoc](https://pandoc.org/installing.html)** — only needed if re-extracting the question bank from `.docx` source files
- **Tesseract OCR binary** — only needed for the fallback OCR engine (not pip-installable):
  ```bash
  sudo apt-get install -y tesseract-ocr   # Debian/Ubuntu
  brew install tesseract                  # macOS
  ```
- **Node.js 18+** and npm — only needed to run the frontend

#### 1. Create a virtual environment and install dependencies

The project's own venv is conventionally named `.venv-paddleocr` (referenced by the Makefile, `api/README.md`, and CI); using that exact name lets those existing helper commands work unmodified, but any venv name works.

```bash
python3 -m venv .venv-paddleocr
source .venv-paddleocr/bin/activate   # .venv-paddleocr\Scripts\activate on Windows

# CPU-only machine (no GPU): install the CPU torch wheel FIRST, or a bare
# `pip install -r requirements.txt` resolves plain PyPI torch (a transitive
# dependency of sentence-transformers) and drags in several GB of nvidia_*
# CUDA packages. Skip this if you have a GPU and want CUDA torch.
pip install torch==2.13.0+cpu torchvision==0.28.0+cpu --index-url https://download.pytorch.org/whl/cpu

pip install -r requirements.txt
```

> `requirements.lock.txt` is the full transitive freeze for reproducing the exact environment byte-for-byte if `requirements.txt` alone doesn't resolve cleanly.

Real object storage (MinIO) additionally needs:

```bash
pip install boto3==1.43.82
```

#### 2. Configure environment variables

```bash
cp .env.example .env
```

`.env.example` documents every variable in place (each with the reasoning behind its default) — read it before editing. At minimum, fill in:

- `PGHOST` / `PGPORT` / `PGDATABASE` — where Postgres is
- `PGADMIN_USER` / `PGADMIN_PASSWORD` — the Postgres **owner** role (e.g. `postgres`); used only for migrations, backups, and seeding
- `APP_DB_USER` / `APP_DB_PASSWORD` — the **application** role that migration `016_application_role.sql` creates; `PGUSER`/`PGPASSWORD` must match these exactly
- `GROQ_API_KEY` — free, no credit card, from [console.groq.com](https://console.groq.com) → API Keys → Create API Key (needed for question generation/rewording and the `llm` scoring signal; not needed if you only run with `--stub`/`--stub-llm`)
- `JWT_SECRET` — required to run the API outside `API_ENV=development` (generate with `python -c 'import secrets; print(secrets.token_urlsafe(48))'`); in development it's auto-generated per process if left unset

> **Do not** set `PGUSER=postgres` for the running application — Postgres exempts superusers from Row-Level Security, which would silently disable every tenant-isolation policy. `PGUSER` must point at the non-superuser role created below.

#### 3. Create the database and apply migrations

Postgres must already exist and be reachable (`createdb ai_evaluation`, or point `PGDATABASE` at an existing one).

```bash
set -a && source .env && set +a

# scripts/migrate.py is the canonical runner: it applies files in numeric
# order and records what it applied in schema_migrations, so --status can
# answer "is this DB current?" without guessing.
python3 scripts/migrate.py --up
```

`--up` applies migrations 001–019 in order, including `016_application_role.sql` (creates the `PGUSER` application role from `APP_DB_USER`/`APP_DB_PASSWORD`) and `017_auth_identity.sql` (auth tables + grants on `users`).

If you're baselining a database that already has some migrations applied by hand (e.g. an older checkout), see `scripts/migrate.py --baseline [--through N]` and [`CLAUDE_CONTEXT.md`](CLAUDE_CONTEXT.md) §8 for the exact sequence.

#### 4. Seed a minimal dataset (recommended for local dev)

```bash
./scripts/reset_and_seed_db.sh --yes
```

This backs up the current data first (to `db_backups/`), truncates all data tables, and loads `migrations/seed_minimal.sql`. It refuses to run unless `PGHOST` is a recognized local/disposable host (`localhost`, `127.0.0.1`, `::1`, `postgres`, `db`) — this script is destructive and is not meant to ever touch a real environment.

#### 5. Create the first login

There is no open signup; every account other than the first is created by an admin from inside the app.

```bash
python3 scripts/bootstrap_platform_admin.py \
    --email admin@platform.example --name "Platform Admin" --dry-run   # preview

python3 scripts/bootstrap_platform_admin.py \
    --email admin@platform.example --name "Platform Admin"             # actually create it
```

You'll be prompted for a password (or set `BOOTSTRAP_ADMIN_PASSWORD` in the environment for the duration of that one command — never on the command line, where it would be visible in `ps`).

#### 6. Run the API and worker

```bash
set -a && source .env && set +a
.venv-paddleocr/bin/uvicorn api.main:app --reload             # http://127.0.0.1:8000/docs
.venv-paddleocr/bin/python scripts/run_job_worker.py --stub   # in another shell; drop --stub once GROQ_API_KEY works and STORAGE_MODE=minio is set up
```

`GET /health` is a liveness probe with no auth/DB required — a quick way to confirm the API is up. Every other endpoint needs `Authorization: Bearer <access token>` (see `api/README.md` for the full 23-endpoint reference and the login flow).

#### (Optional) Real object storage — MinIO

Only needed for `--storage minio` / `STORAGE_MODE=minio` (real diagram scans, real booklet uploads persisted across processes). Without this, everything works in `dummy` storage mode using only Postgres.

```bash
docker compose -f docker-compose.minio.yml up -d   # S3 API on :9000, console on :9001
```

Then in `.env`:

```bash
MINIO_ENDPOINT=http://localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin123
MINIO_BUCKET=ai_evaluation
STORAGE_MODE=minio
```

### Frontend setup

```bash
cd frontend
npm install
npm run dev   # http://localhost:5173
```

`frontend/.env.local` already points the frontend at `http://127.0.0.1:8000` (`VITE_API_BASE_URL`) — change it if your API runs elsewhere. The frontend's default CORS origin (`http://localhost:5173`) is exactly what the API allows by default in `API_ENV=development`.

```bash
npm run build     # production build (tsc -b && vite build)
npm run lint       # oxlint
npm run preview    # preview the production build locally
```

---

## Loading the Question Bank

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

## Running the Test Suite

```bash
make test          # full suite (needs paddleocr/paddlepaddle/sentence-transformers installed)
make test-fast     # skips the sentence-transformer-loading `slow` tests — what CI runs
make test-cov      # + coverage report over core/
```

`make test*` sources `.env` automatically if it exists, and falls back to plain `python3` if `.venv-paddleocr` doesn't exist (matching how CI runs it). `make test` needs real network access the first time a `slow` test downloads the `all-MiniLM-L6-v2` model from Hugging Face; `make test-fast` needs none of that (no `GROQ_API_KEY`, no MinIO, no network).

## Design Principles

- **No blobs in Postgres** — always object storage + URL reference
- **Append-only audit trail** — never overwrite a score or reference answer; insert new row, flip `is_current`
- **Generalized tables over duplication** — one `topic_links` table, one `content_assets` table, not per-type copies
- **Explicit nullability and cardinality** — every FK states whether it can be null and its cardinality

See [`readme files/PROJECT_CONTEXT.md`](readme%20files/PROJECT_CONTEXT.md) §8 for the full list of non-negotiable patterns.

## Environment Variables

`.env.example` is the source of truth — every variable is documented in place with *why* it exists and what happens if it's left unset. Summary:

| Variable | Required | Description |
|---|---|---|
| `PGHOST` / `PGPORT` / `PGDATABASE` | Yes | Postgres connection target |
| `PGUSER` / `PGPASSWORD` | Yes | The **application** role (must match `APP_DB_USER`/`APP_DB_PASSWORD`, never `postgres`) |
| `APP_DB_USER` / `APP_DB_PASSWORD` | Yes | Credentials migration `016_application_role.sql` uses to create the app role |
| `PGADMIN_USER` / `PGADMIN_PASSWORD` | Yes | Postgres owner role — migrations, backups, `reset_and_seed_db.sh` |
| `MINIO_ENDPOINT` / `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` / `MINIO_BUCKET` | Only with `--storage minio` / `STORAGE_MODE=minio` | Object storage connection |
| `GROQ_API_KEY` | Only for real (non-stub) LLM calls | Free-tier key from [console.groq.com](https://console.groq.com) |
| `API_ENV` | No (default `development`) | Gates CORS defaults and debug endpoints |
| `CORS_ORIGINS` | Required outside `development` | Comma-separated allowed origins — no permissive fallback outside dev |
| `JWT_SECRET` | Required outside `development` | Signs access tokens; auto-generated per process in dev |
| `STORAGE_MODE` | No (default `dummy`) | `dummy` (no MinIO/boto3 needed) or `minio` |
| `BOOTSTRAP_ADMIN_PASSWORD` | Only for `bootstrap_platform_admin.py` | Set only for the duration of that one command |

## Troubleshooting

- **`RLS is inert` / a query returns everything regardless of tenant** — `PGUSER` is pointed at a superuser (e.g. `postgres`). Postgres exempts superusers/`BYPASSRLS` roles from Row-Level Security entirely. Point `PGUSER`/`PGPASSWORD` at the application role created by `migrations/016_application_role.sql` instead.
- **A tenant-scoped query returns zero rows unexpectedly** — RLS fails *closed and silently*. This usually means the request never set `app.current_college_id`/`app.is_platform_admin` — check that you're going through `api/deps/db.py`'s connection helpers, not opening a raw connection.
- **`pip install -r requirements.txt` tries to download a huge nvidia_* wheel / fails on a flaky connection** — you skipped the CPU-torch step. Install the CPU wheel first: `pip install torch==2.13.0+cpu torchvision==0.28.0+cpu --index-url https://download.pytorch.org/whl/cpu`, then re-run `pip install -r requirements.txt`.
- **`migrate.py --up` fails on a fresh run against a database with some migrations already applied by hand** — baseline it first: `python3 scripts/migrate.py --baseline` (marks 001–015 applied without re-running them), then `--baseline --through 17` if 016/017 are also already applied, then `--up`.
- **API refuses to start with a CORS or JWT_SECRET error** — expected outside `API_ENV=development`. Set `CORS_ORIGINS` explicitly (there's no permissive fallback in non-dev) and generate a `JWT_SECRET` with `python -c 'import secrets; print(secrets.token_urlsafe(48))'`.
- **Job worker fails on booklet ingestion with "No local bytes for dummy ref"** — the API and the worker disagree on `STORAGE_MODE`/`DUMMY_STORAGE_ROOT`. Both processes must use the same values (dummy mode keeps uploaded bytes on disk under `DUMMY_STORAGE_ROOT` so the worker can read them back later).
- **`tesseract: command not found`** — the Tesseract binary isn't pip-installable; install it via your OS package manager (`apt-get install tesseract-ocr` / `brew install tesseract`). Its absence doesn't crash the pipeline — `core/ocr_fallback.py` just skips that engine.
- **Diagram/table extraction imports fail** — those features need `paddleocr`/`paddlepaddle`/`opencv` installed, which is why the project uses a dedicated `.venv-paddleocr` — a different venv may be missing the exact pinned `paddlepaddle` build the workarounds in `core/paddle_workarounds.py` are tested against.
- **Frontend can't reach the API (CORS or network errors in the browser console)** — check `frontend/.env.local`'s `VITE_API_BASE_URL` matches where the API is actually running, and that the API's `CORS_ORIGINS` (or its dev default) includes the frontend's origin.
- **`reset_and_seed_db.sh` refuses to run** — it only runs against a recognized local/disposable `PGHOST` (`localhost`, `127.0.0.1`, `::1`, `postgres`, `db`) because it truncates every data table. Use `--force-external-host` only against a genuinely disposable database.

## License

Internal use only. Not licensed for external distribution.
