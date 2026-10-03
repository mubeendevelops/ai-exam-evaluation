# Tarn AI Evaluation

Tarn AI Evaluation is a multi-tenant SaaS that helps college teachers evaluate handwritten exam answer booklets. A teacher photographs a booklet and uploads it. The system cleans the pages, reads the handwriting with several OCR engines (best-of-N), splits the booklet into answers and suggests marks for each rubric criterion. AI suggests; the teacher reviews, overrides and approves every mark, and every change is audited.

## Status

Development environment ready (P1): monorepo, Docker Compose stack, tooling and CI. Domain code starts in P2.

## Principles

- **Teacher decides.** No mark is final until the teacher approves it. Amendments after approval create a new result sheet version and keep the old one.
- **Tenant isolation.** Each college is a tenant. Student data carries a college id and is isolated by PostgreSQL row-level security. Questions, keys, rubrics and exam blueprints are shared content with an owning college; other colleges copy instead of editing.
- **Pure core.** Domain logic is a Python package with no I/O. Storage, OCR engines, queues and cloud services plug in as adapters, so the same core runs behind the web API, a CLI or a worker.
- **Non-LLM scoring first.** Keyword and list matching, numeric checks, local sentence embeddings and diagram graph matching. An LLM scorer can be added later as an optional extra.
- **Local data in development.** All images and data stay on the development machine; cloud OCR engines are off by default.

## Stack

| Layer | Technology |
| --- | --- |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Typer, uv |
| Database | PostgreSQL 16 with pgvector and row-level security |
| Queue | PostgreSQL-backed job queue |
| Storage | MinIO (development, built from a pinned source tag), Google Cloud Storage (production) |
| OCR | Tesseract, PaddleOCR, TrOCR; optional cloud engines |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS |
| Production | Google Cloud, asia-south1: Cloud Run, Cloud SQL, Cloud Storage, Cloud KMS, Secret Manager |

## Repository layout

```text
backend/core       domain logic and ports (no I/O)
backend/adapters   PostgreSQL, storage, OCR, embeddings, queue, key management
backend/api        FastAPI application
backend/worker     queue worker
backend/cli        command-line interface
frontend/          web app
deploy/dev/        MinIO build and PostgreSQL init for Docker Compose
deploy/gcp/        Terraform for Google Cloud (later)
docs/              UI references (project_idea.html, MainLogin.html), development.md
```

## Development

Needs Docker with Compose, uv and Node 22.22+ (24 LTS preferred). Details, tooling choices and GPU setup: [docs/development.md](docs/development.md).

```bash
make setup   # install dependencies, create .env
make up      # start the stack: frontend :5173, API :8000/docs, MinIO console :9001
make ci      # lint, typecheck, test
```

| Command | Purpose |
| --- | --- |
| `make setup` | Install Python and Node dependencies, create `.env` from `.env.example` |
| `make up` / `make down` | Start or stop the Docker Compose stack (`make up-gpu` adds the NVIDIA GPU) |
| `make test` / `make test-integration` | Run unit tests / tests that need the running stack |
| `make lint` / `make typecheck` / `make fmt` | ruff, import-linter, eslint, prettier / mypy --strict, tsc / formatters |
| `make migrate` / `make seed` | Placeholders until P3 (migrations) and P8 (seed data) |

Real student booklets are never committed; `samples/` is git-ignored.
