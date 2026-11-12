# Tarn AI Evaluation

A multi-tenant web service that helps college teachers evaluate handwritten exam answer booklets. A teacher photographs a booklet with a phone; the system cleans the pages, reads the handwriting with several OCR engines (best of N per line), splits the booklet into answers and suggests marks per rubric criterion.

**AI suggests; the teacher decides every mark.** Nothing is final until the teacher approves it, and every change is audited. Each college is a tenant: student data is isolated per college; questions, keys, rubrics and exam blueprints are the college's own content too, never shared between colleges (D165).

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

| Institution ID | SME (subject expert) | Content |
| --- | --- | --- |
| `DEMO_COM` | `anita.desai@demo-com.example.test` | QP-CI, QP-IPR, Assignments 1 and 2 (synthetic keys) |
| `DEMO_ENG` | `kavya.shetty@demo-eng.example.test` | AI & ML keys K-AI1 to K-AI3 |

The keys without a faculty source are written for development and carry the badge **SYNTHETIC – dev only, needs teacher validation**.

### Who does what (personas)

Each college has three kinds of account (D159–D164). What each may do is one capability table (`tarn_core/domain/personas.py`), checked by every API route; a persona without the capability gets 403, and another subject's, SME's or college's data looks missing (404).

| Persona | Accounts | Does | Never |
| --- | --- | --- | --- |
| **Admin** | One shared login per college (`admin@demo-com.example.test`); wrong passwords lock the device's address, not the account | SMEs (invite, subjects, suspend, remove), the subject list, the student roster, units of work (paper + student year + subject + one SME + due date), booklet upload by roll number and retakes, transfers and the SMEs' reassignment requests, returning a submitted unit, the dashboard and progress report | Marks, answer text, page images, reviews or result sheets |
| **SME** (subject expert) | Invited by the admin (`anita.desai@…`, `suresh.naik@demo-com.example.test`) | Questions, keys, rubrics and papers of the subjects granted; marks the booklets of the units they own; submits a unit; deletes content once its units are frozen | Another subject's content or another SME's unit |
| **Governor** | One per college, created by the Tarn operator: `tarn tenants governor DEMO_COM --email E --operator NAME` | Freezes and signs off submitted units; reads their frozen record | Anything else for now (the Governor portal is Stage H) |

### The admin portal

Sign in as the admin (`DEMO_COM`, `admin@demo-com.example.test`, the seed's password) and you land on `/admin`, a sidebar of six sections:

1. **Dashboard Overview** – SMEs (Active, Idle, Suspended), units and overdue ones, booklets by stage, recent activity, system health (API, worker, queue, storage used).
2. **SME Directory & Subjects** – the college's subjects (add, rename, retire) and SMEs (add with subjects, edit subjects, suspend, resume, force a reset, unlock, remove). Removing a subject from an SME who has open units in it asks you to transfer them first.
3. **Upload & Assign Papers** – create a unit from a paper an SME wrote (it needs an exam code and every slot linked) for an SME entitled to its subject; open the unit and drop PDFs named by roll number (`1XX21CS001.pdf`, one per student, up to 20 at once). Each file is queued or refused with its reason; an unknown roll number is fixed under **Students** (`/admin/upload/students`: add, edit, CSV import) and the file uploaded again. Flagged pages can be used anyway or the file replaced. A submitted unit shows the SME's submission and **Return to SME**.
4. **SME Progress Reports** – per SME and per unit: booklets, approved, pending, overdue, Heavy load.
5. **Redistribute Workload** – the SMEs' reassignment requests, and a transfer of a whole open unit to another entitled SME.
6. **Audit & External Reports** – "Reports come later".

A unit goes open → submitted (by its SME) → frozen (by the Governor, at `/governor`); the admin may return a submitted unit with a note. Emails go out on assignment, transfer and return (to the API log in development).

### The SME portal

Sign in as an SME (`DEMO_COM`, `suresh.naik@demo-com.example.test`, the seed's password) and you land on `/sme`, a sidebar with two groups (an SME sees only the subjects the admin granted and the units they own):

1. **My Assignments** (`/sme`) – how many units you own (lifetime and this academic year, from 1 July), completed and active, the Frozen / Submitted / Open breakdown and your average turnaround; the table of your units with sort and filter, and per unit **Evaluate**, **Delegate** (ask the admin to move it, optionally suggesting a colleague of the subject) and **Submit**. Evaluate opens the unit's **student list** (filter by name, roll number or status), a student opens their **question list**, a question opens the evaluation screen (the reference beside the student's text, the AI suggestion with per-criterion reasons, override, tags, remarks, Approve; "Pages and segments" for the machine's reading and split). A completed booklet shows "View Final" with every result-sheet version.
2. **Manage Question Papers** (`/sme/papers`) – papers of your subjects with search, date and descriptor filters; create (blank or from a schema), clone, edit (sections, any N of M, OR pairs, sub-parts, the exam code, and each slot linked to a question through a search or "New question"), preview and download the question paper PDF, delete once no unfrozen unit uses it. Once booklets are uploaded against a paper, only its title, descriptor, description, target batch, duration and course code may change: clone it to change the structure. **Questions** opens the question bank's search and the question editor (text, marks, rubric, reference answers, glossary, key files, reference diagrams), which warns before saving a question that papers use.
3. **Manage Schema** (`/sme/schemas`) – reusable paper templates (sections and marks, no questions linked) with a target batch and a weightage description; add, clone, edit, delete, and **Generate Question Paper**.
4. **My Profile** (`/sme/profile`) – your name, email, employee ID, department (the admin keeps these) and subjects; change your password, recovery codes.
5. **My Reports** (`/sme/reports`) – average turnaround, on-time rate, units submitted and booklets approved in the last four quarters, open work. No marks, speed or accuracy score.

The header has a **search** over your units, their students, your subjects' papers, schemas and questions, and a **bell** with your notices: a unit assigned to you or moved away, returned with the admin's note, your reassignment request done or declined, due in 3 days (also emailed) and overdue. The worker writes the due-date notices once a day (`TARN_REMINDER_INTERVAL_HOURS`, 24 by default).

Several of the SME portal's rules are provisional (D215–D228) until the boss confirms them; see `docs/acceptance.md` section 4.

Registering a new college: the public page, then the emailed verification link, then a Tarn operator approves it: `cd backend && uv run tarn tenants list` and `uv run tarn tenants approve <ID> --operator <you>`.

## Command line

Everything runs from `backend/` as `uv run tarn …`.

| Task | Command |
| --- | --- |
| Run one booklet without the web app, database or queue | `tarn evaluate FOLDER\|- --college DEMO_COM --exam QP-CI [--usn U] [--out DIR] [--use-anyway]` → `result.json` and `result-sheet-draft.pdf` (AI suggestions, stamped DRAFT). `-` reads a PDF, image or tar from stdin. `tarn exams` lists the exams and USNs |
| Page cleaning check | `tarn pages check FILE…` |
| OCR | `tarn ocr models check\|fetch`, `tarn ocr read FILE…`, `tarn ocr calibrate MANIFEST` |
| Benchmarks | `tarn bench ocr DIR`, `tarn bench segment DIR`, `tarn score bench SET`, `tarn score calibrate SET`, `tarn diagram bench` |
| Tenants (Tarn operator) | `tarn tenants list\|approve\|suspend\|resume ID --operator NAME`; `tarn tenants governor ID --email E --operator NAME`; `tarn tenants llm ID --on\|--off --operator NAME` |
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
- **Papers are filled by hand.** Generating a paper from a schema copies its sections and marks; AI filling, MCQs and bulk question import are not built (D169, D221).

- **The admin and SME portals are built; the Governor portal is not.** The Governor has a freeze page only. The SME portal's provisional rules (D215–D228) await the boss's answers. Reports, AI question generation and storage limits come later.

The full list is in `docs/acceptance.md` and, for contributors, `CLAUDE.md`.

## What comes next

1. **Stage H — Governor portal:** analytics, audits, freeze and sign-off (publishing), report requests; it replaces the `/governor` freeze page. Its open questions are in `docs/notes/later_stages.md`.
2. **The boss's answers on the SME portal** (D215–D228): each change becomes a new decision and the screens follow.
3. **Later, on request:** aggregated reports (signed PDFs, D168), AI question and answer generation (D169), the Provisioner (storage limits, colleges).

What is known about each, and the questions still open, is in `docs/notes/later_stages.md` (kept locally).
