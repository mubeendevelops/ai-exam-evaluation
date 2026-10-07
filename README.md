# Tarn AI Evaluation

A multi-tenant web service that helps college teachers evaluate handwritten exam answer booklets. A teacher photographs a booklet with a phone; the system cleans the pages, reads the handwriting with several OCR engines (best of N per line), splits the booklet into answers and suggests marks per rubric criterion.

**AI suggests; the teacher decides every mark.** Nothing is final until the teacher approves it, and every change is audited. Each college is a tenant: student data is isolated per college; questions, keys, rubrics and exam blueprints are global content with an owning college.

Built as a pure Python core behind ports and adapters, with a FastAPI API, a queue worker, a CLI and a React web app. Production target: Google Cloud `asia-south1` (not yet deployed).

- Status and what was checked against the prototype and requirements: [`docs/acceptance.md`](docs/acceptance.md)
- Measured error rates: [`docs/benchmarks/summary.md`](docs/benchmarks/summary.md)
- Security review: [`docs/security-review.md`](docs/security-review.md)
- Development environment details (GPU, OCR engines, CLI, queue): [`docs/development.md`](docs/development.md)
- Google Cloud deployment: [`deploy/gcp/README.md`](deploy/gcp/README.md)
- API contract: [`docs/api/openapi.json`](docs/api/openapi.json); exam blueprint JSON Schema: [`docs/api/blueprint.schema.json`](docs/api/blueprint.schema.json)

## Setup

Prerequisites: Docker with Compose v2, [uv](https://docs.astral.sh/uv/) 0.12.x, Node.js 22.22+ or 24 LTS (`frontend/.nvmrc`), `make`. Python 3.12.14 is downloaded by uv. An NVIDIA GPU is optional (TrOCR runs on the CPU without one). The first `make up` builds MinIO from source and the worker image with the OCR stack (the worker image with CUDA torch is about 11 GB): leave plenty of free disk.

```bash
make setup      # uv sync, npm ci, copies .env.example to .env
make up         # PostgreSQL + pgvector, MinIO, API, worker, web app (make up-gpu gives the worker the GPU)
make migrate    # application database and the separate identity database
make seed       # demo colleges, papers, keys, rosters (development only; refused in production)
make down       # stop, keeping the data volumes
```

OCR and scoring models are downloaded once into `var/models/` (git-ignored):

```bash
cd backend
uv run tarn ocr models fetch      # PaddleOCR, TrOCR, Tesseract check
uv run tarn score models fetch    # sentence-embedding model for scoring
uv run tarn doctor --services     # what is reachable, which engines run, which are skipped and why
```

| Service | URL |
| --- | --- |
| Web app | http://localhost:5173 |
| API docs | http://localhost:8000/docs |
| MinIO console | http://localhost:9001 |

All ports bind to 127.0.0.1. Verification, invitation and reset emails go to the API log in development: `docker compose logs api | grep token=`.

## Run it

`make seed` creates two fictitious colleges and prints the development password. Sign in on http://localhost:5173 with an Institution ID and an email:

| Institution ID | Teacher | Content |
| --- | --- | --- |
| `DEMO_COM` | `pooja.rao@demo-com.example.test` | QP-CI, QP-IPR, Assignments 1 and 2 (synthetic keys) |
| `DEMO_ENG` | `kavya.shetty@demo-eng.example.test` | AI & ML keys K-AI1 to K-AI3 |

(Admins: `admin@demo-com.example.test`, `admin@demo-eng.example.test`.) The keys without a faculty source are written for development and carry the badge **SYNTHETIC – dev only, needs teacher validation**.

The teacher's path through the three tabs:

1. **Q&A DB** – questions, benchmark answers, rubrics, glossaries, reference diagrams and key files.
2. **AI Evaluation** – step 1 pick the student and exam and drop the booklet (a PDF, or page images in order; one drop is one booklet); step 2 check the segmentation (the machine reads and splits it; the teacher can correct lines, split, merge and reassign); step 3 approve answer by answer (AI suggestion, reference beside the student's text, per-criterion reasons, override in the paper's mark step), then approve the booklet.
3. **Evaluated** – approved booklets and their result-sheet PDFs (a new version when an approved booklet is amended).
4. **Schema Designer** – exam blueprints (any N of M, OR pairs, sub-parts, per-step marks); the JSON is validated by the server.

Registering a new college: the public page, then the emailed verification link, then a Tarn operator approves it: `cd backend && uv run tarn tenants list` and `uv run tarn tenants approve <ID> --operator <you>`.

## Command line

Everything runs from `backend/` as `uv run tarn …`.

| Task | Command |
| --- | --- |
| Run one booklet without the web app, database or queue | `tarn evaluate FOLDER\|- --college DEMO_COM --exam QP-CI [--usn U] [--out DIR] [--use-anyway]` → `result.json` and `result-sheet-draft.pdf` (AI suggestions, stamped DRAFT). `-` reads a PDF, image or tar from stdin. `tarn exams` lists the exams and USNs |
| Page cleaning check | `tarn pages check FILE…` |
| OCR | `tarn ocr models check\|fetch`, `tarn ocr read FILE…`, `tarn ocr calibrate MANIFEST` |
| Benchmarks | `tarn bench ocr DIR`, `tarn bench segment DIR`, `tarn score bench SET`, `tarn score calibrate SET`, `tarn diagram bench` |
| Tenants (Tarn operator) | `tarn tenants list\|approve\|suspend\|resume ID --operator NAME`; `tarn tenants llm ID --on\|--off --operator NAME` |
| Database and identity | `tarn db …`, `tarn identity export\|import`, `tarn seed` |

`tarn evaluate` uses the demo seed's exams and the core's in-memory adapters (no PostgreSQL, MinIO or API), and never calls the LLM. Use synthetic pages, not real student booklets, outside git-ignored folders.

## Switches that send data off the machine

Everything is local by default. In development, student data never leaves the machine unless you turn these on, each of which needs several switches.

- **Cloud OCR engines** (Amazon Textract, Azure Document Intelligence Read, Google Document AI): `TARN_CLOUD_OCR_ENABLED=true`, and outside production also `TARN_CLOUD_OCR_ALLOW_IN_DEVELOPMENT=true`, the SDKs (`cd backend && uv sync --group cloud-ocr`) and credentials: Textract uses the AWS credential chain in `TARN_AWS_REGION`; Azure needs `TARN_AZURE_DI_ENDPOINT` and `TARN_AZURE_DI_KEY`; Document AI needs `TARN_DOCAI_PROCESSOR` and Application Default Credentials. `ocr.engines` in the worker log says which engines run and why the others were skipped. Whole pages go to the provider: settle the provider's data terms first.
- **LLM second-opinion scorer** (Groq): off by default and never the mark. Needs `TARN_LLM_SCORER_ENABLED=true`, `TARN_LLM_ALLOW_IN_DEVELOPMENT=true`, `GROQ_API_KEYS`, and the college switched on by an operator (`tarn tenants llm ID --on --operator NAME`). Only answer text (never names or USNs) is sent. Several keys are for development only; production needs one key of a paid plan and refuses several. Use synthetic answers in development.

`.env.example` lists every variable with a safe default.

## Quality checks

```bash
make ci                # openapi check, lint (ruff, import-linter), typecheck (mypy --strict), tests
make test-integration  # needs make up: PostgreSQL row-level security, queue, MinIO, API end to end
make test-models       # the real OCR engines on generated pages (about 6 minutes)
make test-e2e          # Playwright against the built app with a stubbed API
```

## Layout

```text
backend/    tarn_core (pure: domain, ports, services) · tarn_adapters (PostgreSQL, MinIO/GCS, OCR, embeddings, diagrams, PDFs, LLM)
            tarn_api (FastAPI) · tarn_worker (job queue worker, Cloud Run job) · tarn_cli (`tarn`)
frontend/   React 19 + TypeScript + Vite + Tailwind (the web app; the API client is generated from the OpenAPI document)
deploy/     dev/ (MinIO, PostgreSQL init) · gcp/ (Terraform, never applied)
docs/       api/ (OpenAPI, JSON Schemas) · auth/ · benchmarks/ · acceptance.md · security-review.md · development.md
scripts/    dataset and seed helpers
```

`tarn_core` imports no web, database, cloud or ML library (import-linter enforces it), so the same core runs behind the API, the worker and the CLI.

## Known gaps

These are stated plainly because they bound what the system can claim today.

- **University answer booklet layout is untested (C18).** None of the samples is a real university booklet: cover page, ruling, margins and question boxes have not been seen.
- **Production compute is undecided.** There is no GPU budget; the local engines may run on the CPU in production (about 30 s per page on the CPU against 12 s on the development GPU, see the benchmark summary). Document AI and Cloud Run GPUs in `asia-south1` are limited-access. Nothing has run on Google Cloud or AWS: the Terraform is validated only and the cloud adapters ran on fakes.
- **Batch scale beyond 5 booklets is deferred (C30).** At most 5 booklets per teacher are queued and the worker processes one at a time. Uploads are held in memory up to a size cap, and Cloud Run takes 32 MiB per request, so large booklets need direct-to-bucket uploads.
- **Keys are synthetic.** Faculty keys for QP-CI, QP-IPR and the Assignments are missing; every OCR, scoring and flag threshold is a placeholder until teacher-marked booklets exist. Scoring was calibrated on a public typed-answer set, not on teacher marks.
- **Handwritten diagrams, formulas and computations** have no real samples; the diagram detector was trained on public flowchart sets. Circuits, plots and labelled drawings are not scored.
- **The Schema Designer does not save blueprints from the page** (the API does); paper auto-generation, bulk question import and a saved-schema library from the prototype are not built.

The full list is in `docs/acceptance.md` and, for contributors, `CLAUDE.md`.
