# CLEANUP_AUDIT.md — repository cleanup audit

**Read-only audit. Nothing in this session was deleted, moved, or edited.**
Produced 2026-09-09 against `main` @ `a4d067b`, working tree clean.

## How this was produced

- **Dead code:** no `vulture` or `pyflakes` is installed in `.venv-paddleocr`, and
  installing one would have mutated the venv, so a purpose-built `ast` walker was
  used instead: it collects every module-level `def`/`class`/`CONST` in `core/`,
  `api/`, `scripts/` (952 symbols), then counts every `Name`, `Attribute` and
  `ImportFrom` reference to each across `core/ api/ scripts/ tests/`. A symbol is a
  candidate only at **zero references anywhere**, then confirmed by a repo-wide
  `grep` over `.py`, `.md`, `.sh`, `.yml`, `.txt` and `Makefile`.
  `ruff check --select F401,F811,F841` was also run (one hit, listed below).
- **Duplication:** normalized-line `difflib.SequenceMatcher` over every file pair in
  `core/ api/ scripts/`, docstrings and comments stripped, minimum run 7 code lines.
- **Coverage:** a real run — `pytest -m "not slow" --cov=core --cov=api` against the
  live local Postgres. See the caveat under §6.
- **Everything else:** `git ls-files`, `git check-ignore`, and direct cross-reference
  against `CLAUDE_CONTEXT.md`, `README.md`, `api/README.md`,
  `readme files/SCRIPT_COMMANDS.md`, `Makefile`, `docker-compose.yml`, `Dockerfile`
  and `.github/workflows/ci.yml`.

**Confidence key.** *high* = statically confirmed and manually verified; *medium* =
confirmed but the right action is a judgment call; *low* = flagged for a human, listed
separately in §8 and **not** cleared for any cleanup prompt.

---

## 1. Dead code

The codebase is unusually clean here. Of 952 module-level symbols in `core/`, `api/`
and `scripts/`, exactly **three** have no reference anywhere in the tree.

| Item | Location | Confidence | Reasoning |
|---|---|---|---|
| `roles_description(roles)` | `api/deps/identity.py:447` | high | Zero references in any `.py`, `.md`, `.sh`, `.yml` or `Makefile` — its own `def` line is the only occurrence in the repo. Docstring says it builds OpenAPI `responses` descriptions; no router ever calls it. Removing it also frees the only use of `Iterable`, imported at `api/deps/identity.py:84`. |
| `_b64(raw)` | `core/users.py:102` | high | Zero references. Private, so it cannot have an external caller. `base64` stays imported for `b64decode` at `core/users.py:185-186`, so the import is not dead — only this function is. Almost certainly a leftover from writing the legacy-scrypt verifier. |
| `get_admin_conn()` | `api/deps/db.py:341` | medium | No `Depends(get_admin_conn)` and no direct call anywhere in `api/`, `core/`, `scripts/` or `tests/`. Only mentions are prose. **But it is deliberate, documented API surface** (`api/routers/__init__.py:9`, `api/README.md:145`, CLAUDE_CONTEXT §3/§6) and its own docstring explains it is unwired on purpose. Treat as *document-or-decide*, not *delete*: see §7 for the stale half of that docstring. |
| `second = ...` assigned, never used | `tests/test_api/test_evaluation.py:440` | high | Sole `ruff --select F401,F811,F841` hit in the tree. Test-local, harmless, one line. |

### Flagged, NOT dead (per the brief)

| Item | Location | Why it looked dead | Verdict |
|---|---|---|---|
| `DiagramEvaluationPlugin`, `TableExtractionPlugin`, `TextExtractionPlugin` | `core/plugins/diagram_evaluation.py:81`, `table_extraction.py:66`, `text_extraction.py:283` | Zero static references to the class names | **Dynamically registered.** `@register` (`core/plugins/registry.py:54`) plus `importlib.import_module` at `registry.py:105` loads every non-framework module in `core/plugins/`. Deleting any is a silent capability loss, not a lint fix. |
| `_RequestIdFilter.filter`, `JsonFormatter.format` | `api/logging_config.py:109`, `:128` | Zero call sites | Called by the stdlib `logging` machinery, not by this repo. |
| Every FastAPI route handler (`login`, `get_result`, `upload_booklet`, …) | `api/routers/*.py` | Zero call sites | Invoked by Starlette through the `@router.*` decorator. |
| All 24 files under `scripts/` | — | Never imported as modules | CLI entry points. Every one has an `if __name__ == "__main__"` guard; several are additionally imported by `api/services/questions.py` and by tests via `importlib.util.spec_from_file_location` (`tests/test_api/test_jobs.py:518`, `test_evaluation.py:48`). **`scripts/` importing `core/` is §11 rule 1's documented exception and is expected — no `scripts/` file is listed as dead here.** |
| `core/pattern_tree.py` (whole module) | — | 0% test coverage | Imported and used by `scripts/view_pattern.py`. Live code, untested code — see §6, not §1. |

### Referenced only from `.md` — stale doc, not dead code

| Item | Location | Confidence | Reasoning |
|---|---|---|---|
| `get_admin_conn` | `api/deps/db.py:341` | medium | The only non-definition references are documentation (`api/README.md:145,261`, `api/routers/__init__.py:9`, `CLAUDE_CONTEXT.md` §3/§6, `tests/test_api/test_rls_isolation.py:993,998` — docstring prose, not a call). **The docs are not stale about its existence; they are stale about its rationale.** Its docstring's reason for being unwired ("admin authorization does not exist yet … identity.py always reports is_platform_admin=False") stopped being true on 2026-09-06 when auth landed with a real `platform_admin` role. Fix the docstring first, then decide whether to wire or drop it. |

---

## 2. Duplicated logic

`enable_mkldnn=False` — **RE-7 is clean, and no duplication of that shape survives.**
`core/paddle_workarounds.py` is the sole definition (`ENABLE_MKLDNN`, `quiet_paddle()`,
`construct()`), and both former copy-paste sites now import from it:
`core/ocr_engines/paddleocr_engine.py:16` and `core/booklet_segmenter.py:73`. The only
other occurrence of the string in the tree is prose in `core/table_extractor.py:88`,
which correctly explains that that module constructs no Paddle model and so has nothing
to import.

The pattern *did* recur elsewhere. Findings, largest first:

| Item | Location | Confidence | Reasoning |
|---|---|---|---|
| Sliding-window rate-limiter internals copy-pasted verbatim | `api/deps/quota.py:126-143` ↔ `api/deps/ratelimit.py:145-162` | high | 16 identical lines: `_bucket`, `_prune`, `_retry_after_one`. **Exactly the RE-7 shape.** `quota.py` already imports `MAX_TRACKED_KEYS` from `ratelimit.py` (line 84) and its docstring (line 47) claims the mechanism was *"generalized"* — it was duplicated. `SlidingWindowLimiter` should be the one class and `LoginRateLimiter` should subclass or hold it. |
| Two near-identical single-block evaluation CLIs | `scripts/evaluate_diagram_answer.py` ↔ `scripts/evaluate_table_answer.py` | high | Five separate identical runs: `165-188`↔`152-175` (19 lines: the `transition_to_ai_scored` → `persistence.write_evaluation_result` → return-dict tail), `143-151`↔`135-143` (8: the `structured_data`/`marks_max` lookup), `61-69`↔`56-64` (8: the `sys.path` preamble), `316-324`↔`286-294` (9: rollback/close error handling), `334-344`↔`306-316` (8: the dry-run epilogue). ~52 duplicated lines between two files. §7B already names this pair as needing a generalized path. |
| Two near-identical reference-asset loaders | `scripts/load_reference_diagram.py` ↔ `scripts/load_reference_table.py` | high | `79-114`↔`162-197` (19 lines: `uuid4` + the `question_id`/`variant_id` FK-existence checks), `132-142`↔`221-231` (9: dry-run epilogue), `27-36`↔`51-60` (8: preamble + `SCHEMA_VERSION`). The FK-validation block is the substantive half and belongs in `core/`. |
| Benchmark-generator font/style argument handling | `scripts/generate_diagram_benchmark.py:395-413` ↔ `scripts/generate_table_benchmark.py:240-258` | medium | 16 identical lines: glob `FONTS_DIR` for `*.ttf`, filter to `DEFAULT_STYLES` unless `--all-styles`, `SystemExit` on missing styles. Offline fixture tooling, so lower stakes — but three generators share the fixture set and only two share this code. |
| Upload resolution + `UploadNotFoundError` message | `api/services/evaluation.py:98-118` ↔ `api/services/ingestion.py:87-105` ↔ `api/services/ingestion.py:282-308` | high | 9 identical lines across **three** sites, including a verbatim multi-line error string citing `CLAUDE_CONTEXT.md §6`. Three copies of one message drift the fastest. |
| Lazy `FallbackOCR` singleton | `core/diagram_extractor.py:167-173` ↔ `core/table_extractor.py:190-196` | high | 7 identical lines: `_fallback_ocr = None` + `_get_fallback_ocr()` with the `global` and the deferred `from core.ocr_fallback import FallbackOCR`. `core/table_extractor.py:195` even cross-references the other copy in its docstring — acknowledged duplication. Two separate process-wide singletons of the same engine set. Same shape as RE-7; belongs in `core/ocr_fallback.py`. |
| `dry_run` → rollback/commit/close transaction epilogue | `core/booklet_evaluator.py:1240-1249` ↔ `core/plugins/persistence.py:173-182` ↔ `core/booklet_persist.py:274-281` ↔ `scripts/run_job_worker.py:167-174` | medium | 8–9 identical lines at four sites. A `@contextmanager` would collapse all four, but these are short and each sits in a different transaction discipline — worth a look, not an automatic merge. |
| `college_id`/`exam_id`/`student_id` WHERE-clause builder | `core/booklet_summary.py:155-163` ↔ `core/results.py:101-109` | high | 8 identical lines. `core/booklet_summary.py` is from the newest commit (`a4d067b`) — **freshly introduced duplication**, easiest to fix now. Both build the same tenant-scoped predicate over `answers`. |
| Paper/pattern slot-tree assembly | `core/paper_generator.py:123-131` ↔ `scripts/view_paper.py:130-138` | medium | 9 identical lines assembling `children`/`top_slots`. A `scripts/` copy of `core/` tree logic — the shape §11 rule 1 exists to prevent, though here the script is the reader, not the writer. `core/pattern_tree.py` is the natural home. |
| `--weights` parse + `SystemExit` | `scripts/evaluate_answer.py:200-208` ↔ `scripts/evaluate_pending.py:77-85` | low | 8 lines, and much of it is `argparse` boilerplate. See §8. |
| Platform-admin connection preamble | `scripts/evaluate_pending.py:87-97` ↔ `scripts/evaluate_pending_diagrams.py:194-204` | low | 9 lines: `get_connection()` → `SET LOCAL app.is_platform_admin` → `_find_pending` → rollback. §7 explicitly describes the diagram batch runner as *mirroring* `evaluate_pending.py`, so this is intentional parallelism. See §8. |

---

## 3. Stale / generated files tracked in git

**RE-6 held.** `report.json` is no longer tracked (`git ls-files --error-unmatch report.json`
→ *did not match any file known to git*), and `/report.json` is ignored at `.gitignore:44`.
The 44 KB file still sits in the working tree, but it is untracked and correctly ignored —
a local artifact, not a repo problem. Nothing else RE-6 targeted has come back.

The tree is otherwise clean: **no** tracked `__pycache__`, `*.pyc`, `.ipynb_checkpoints`,
`*.orig/.rej/.bak/.swp`, `.egg-info`, `dist/`, `build/`, `node_modules/`, `.pytest_cache/`
or `.ruff_cache/` (ruff self-ignores via its own `.ruff_cache/.gitignore`). `db_backups/`
and `extracted/` are ignored and hold no tracked files. `git status` is clean.

One finding, and it is the most serious item in this report:

| Item | Location | Confidence | Reasoning |
|---|---|---|---|
| **`booklets/` — 28 MB of real student submissions tracked in git** | `booklets/` (10 files: 9 PDFs + 1 `.docx`) | high | Referenced by **nothing**: not `.gitignore`, not `CLAUDE_CONTEXT.md` (§3's tree does not list the directory at all), not `README.md`, not any script, test, `Makefile` target or compose service. The only `booklets/` strings in code are the unrelated storage key prefixes `booklets/pages`, `booklets/source`, `booklets/regions` (`core/booklet_ingest.py:63-64`, `core/booklet_pipeline.py:55`). Filenames carry apparent student names and roll numbers (e.g. `CMS20BF0015 NISHA SINGH.pdf`, `MOHAMMED NIHAL CMS18BC0021 IPR.pdf`, `Assignment_184508_StudentSubmission_…pdf`). `.gitignore:110-114` states *"media/booklets/ also holds sample_booklet.pdf — **the only PDF in this repo**"*, which is now false and is direct evidence this directory arrived unnoticed. **This is a privacy question before it is a cleanup question — do not `git rm` it as routine tidying.** Untracking alone does not remove it from history. |
| `media/ocr_benchmark/`, `media/diagram_benchmark/`, `media/tables/`, `media/booklets/` | ~90 tracked files | high | **Correctly tracked — no action.** They look like generator output, but `.gitignore:105-114` explicitly documents that they are deliberately not ignored, because they are the *labelled fixture sets* `benchmark_ocr_engines.py`, `benchmark_diagram_extraction.py` and `benchmark_booklet_segmentation.py` score against. Listed here only so a later pass does not "fix" them. |

---

## 4. Unreferenced scripts

**Zero genuinely abandoned scripts.** All 31 files under `scripts/` (28 `.py`, 3 `.sh`)
are mentioned in `CLAUDE_CONTEXT.md` at least twice, and every `.py` has a
`__main__` guard. Nothing here is a deletion candidate.

The real finding is the inverse: `CLAUDE_CONTEXT.md` §3 calls
`readme files/SCRIPT_COMMANDS.md` the *"verified CLI command reference for **every**
script"*, and six are missing from it. All six are **operator-run and undocumented —
a documentation fix, not deletion**, and each is identifiably so:

| Item | Location | Confidence | Reasoning |
|---|---|---|---|
| `bootstrap_platform_admin.py` | `scripts/` | high | Documented-but-undocumented: 5 mentions in `CLAUDE_CONTEXT.md` (§4 has a full invocation) and 2 in `api/README.md`, 0 in `SCRIPT_COMMANDS.md`. It is the one-time deployment bootstrap — the most operator-run script in the repo. |
| `migrate.py` | `scripts/` | high | 16 mentions in `CLAUDE_CONTEXT.md`, invoked by `Dockerfile`, `docker-compose.yml` and `.github/workflows/ci.yml`, 0 in `SCRIPT_COMMANDS.md`. Canonical migration runner — clearly live. |
| `run_job_worker.py` | `scripts/` | high | 7 mentions in `CLAUDE_CONTEXT.md`, 5 in `api/README.md`, run by `docker-compose.yml`'s `worker` service, exercised by two tests, 0 in `SCRIPT_COMMANDS.md`. |
| `load_reference_table.py` | `scripts/` | high | 3 mentions in `CLAUDE_CONTEXT.md` §7B (the documented first step of table evaluation), 0 in `SCRIPT_COMMANDS.md`. Its diagram twin `load_reference_diagram.py` *is* documented there — an omission, not a decision. |
| `generate_table_benchmark.py` | `scripts/` | medium | 4 mentions in `CLAUDE_CONTEXT.md`, 0 in `SCRIPT_COMMANDS.md`. Offline fixture tooling, run rarely; its diagram and OCR equivalents are documented there. |
| `run_table_eval_demo.sh` | `scripts/` | medium | 4 mentions in `CLAUDE_CONTEXT.md`, 0 in `SCRIPT_COMMANDS.md`, and no other script or target invokes it. Its diagram twin `run_diagram_eval_demo.sh` *is* documented there. Both demos hardcode DB ids that §7B notes were created by an ad-hoc insert, so this one may genuinely have rotted — verify the ids resolve before assuming it still runs. |

Separately: `README.md` mentions only 2 of the 31 scripts (`extract_exam_bank.py`,
`load_exam_bank.py`). That is part of the broader README staleness in §7, not a
per-script problem.

---

## 5. Dead config / env vars

| Item | Location | Confidence | Reasoning |
|---|---|---|---|
| `pghost`, `pgport`, `pgdatabase`, `pguser`, `pgpassword` | `api/settings.py:40-44` | medium | Declared as pydantic-settings fields with **zero attribute reads** anywhere outside `settings.py`. `core/db.py` reads `os.environ` directly, which `settings.py`'s own docstring says is deliberate. But the docstring's stated purpose — *"so the API can validate configuration at startup"* — is not met: each field has a permissive default and no validator, so nothing is validated. They are documentation typed as config. |
| `minio_endpoint`, `minio_access_key`, `minio_secret_key`, `minio_bucket` | `api/settings.py:47-50` | medium | Same: zero reads; `core/storage.py` goes to `os.environ`. |
| `groq_api_key`, `groq_model` | `api/settings.py:53-54` | medium | Same: zero reads; `core/llm.py:372` and `core/diagram_evaluator.py:492` read `os.environ["GROQ_MODEL"]`. |
| `pguser: str = "postgres"` default | `api/settings.py:43` | medium | Beyond being unread, the default directly contradicts §6 and migration 016: `postgres` is a superuser, and a superuser silently disables every RLS policy. Inert today *because* nothing reads the field — but it is the wrong value to leave written down as the default. |
| `API_MAX_UPLOAD_BYTES` named in a 413 response | `api/routers/upload.py:215` | high | **This key does not exist.** `Settings` sets no `env_prefix`, and the real key is `MAX_UPLOAD_BYTES` (`.env.example:104`, `api/settings.py:67`). An operator who hits the size cap is told to tune a variable that has no effect. One-word fix. |
| `PADDLEOCR_DET_MODEL`, `PADDLEOCR_REC_MODEL`, `PADDLEOCR_LAYOUT_MODEL` | `core/ocr_engines/paddleocr_engine.py:22-23`, `core/booklet_segmenter.py:89` | high | The inverse problem: read from `os.environ` in live code, absent from `.env.example`, `docker-compose.yml`, `Dockerfile` and CI. `api/settings.py`'s docstring states *"Adding a key here without adding it to .env.example is a bug: .env.example is the documented setup contract."* Three live model-selection overrides are undiscoverable. |

**Correction (2026-09-12): the `pghost`/`minio_*`/`groq_*` Settings-field
findings above are false positives, not dead code.** Re-checked by reading
`api/settings.py` in full rather than trusting the symbol-reference count: all
eleven fields ARE read, inside `api/settings.py::load_dotenv_once()`
(lines 254-268), which copies them into `os.environ` so `core/db.py`,
`core/storage.py` and `core/llm.py` see the same values the API was
configured with. `load_dotenv_once()` itself is called from `api/main.py:66`.
The original ast walker's reference count only looked for reads *outside*
`settings.py`, and missed the read site inside the same file — the same class
of gap it (correctly) had to be reconfirmed against for `get_admin_conn`
above. **Nothing in this section should be removed from `.env.example` or
`Settings`.** The one still-real issue nearby is `pguser`'s wrong default
(`"postgres"`, a superuser — contradicts §6 and migration 016); that's a
correctness fix, not a dead-code removal, and stays a judgment call per §8.

Every other key in `.env.example` has a confirmed read site: `API_ENV`, `STORAGE_MODE`,
`DUMMY_STORAGE_ROOT`, `CORS_ORIGINS`, `ENABLE_DEBUG_ENDPOINTS`, `MAX_UPLOAD_BYTES`,
`JWT_SECRET`, `JWT_ALGORITHM`, `ACCESS_TOKEN_TTL_MINUTES`, `REFRESH_TOKEN_TTL_DAYS`,
all eight `*_RATE_LIMIT_*` pairs, `MAX_QUEUED_JOBS_PER_COLLEGE`, `PG*`, `PGADMIN_*`,
`APP_DB_USER`, `APP_DB_PASSWORD`, `BOOTSTRAP_ADMIN_PASSWORD`, `MINIO_*`, `GROQ_*`.
No dead keys in `.env.example` itself.

---

## 6. Test coverage

**No test file is orphaned.** Eight test files have no same-named source module
(`test_cors.py`, `test_observability.py`, `test_rate_limit.py`, `test_rls_isolation.py`,
`test_tenant_context.py`, `test_diagram_plugin.py`, `test_plugin_registry.py`,
`test_text_plugin.py`) — all are behaviour-named suites covering real code, not
leftovers from deleted modules.

**Nothing in this repo is marked coverage-exempt.** There is no `.coveragerc`,
`pyproject.toml` or `setup.cfg`, and `pytest.ini` sets no `omit`. So every gap below is
an unmarked gap.

Measured: `pytest -m "not slow" --cov=core --cov=api` → **83% overall (5181 stmts,
857 missed)**, 26 modules at 100%.

| Item | Location | Confidence | Reasoning |
|---|---|---|---|
| `core/ocr_engines/paddleocr_engine.py` — **0%** | 53 stmts, lines 11-90 | high | Not one line executed. `tests/test_ocr_fallback.py` covers the `FallbackOCR` selection layer (92%) with fakes, never a real engine. The engine that actually reads student handwriting is untested. |
| `core/ocr_engines/tesseract_engine.py` — **0%** | 53 stmts, lines 19-114 | high | Same. §7 says this engine "raises a clear error and is skipped if the binary is missing" — that documented graceful-degradation path has no test. |
| `core/pattern_tree.py` — **0%** | 25 stmts, lines 13-73 | high | Whole module unexecuted. Live code (`scripts/view_pattern.py` imports it), just untested. Smallest module here — cheapest gap to close. |
| `core/diagram_extractor.py` — **21%** | 92 stmts, 73 missed | high | `extract_diagram_structure()` (lines 338-423), the §7 headline function, is entirely uncovered. The benchmark scripts exercise it, but they are not tests and CI never runs them. |
| `core/db.py` — **43%** | 23 stmts, 13 missed | medium | `get_admin_connection()` and most of `get_connection()`'s body (lines 58-71) uncovered — every test uses its own fixture connection. |
| `core/storage.py` — **54%** | 67 stmts, 31 missed | medium | The real-MinIO half (`get_client`, `ensure_bucket`, `upload_file`, `presigned_get_url`) is untested; only the dummy path runs. `presigned_get_url` is load-bearing for `GET /results/{id}/pages/{n}/image`. |
| `core/paddle_workarounds.py` — **55%** | 20 stmts, lines 76-83, 95-96 | medium | RE-7's centralized `quiet_paddle()` and `construct()` bodies are uncovered — the one place a paddlepaddle bump would break. §10 says re-testing the workaround "is not optional before ever bumping the pin"; there is no test to re-run. |
| `core/users.py` — **60%** | 124 stmts, 50 missed | high | Lines 306-344 and the legacy-scrypt verifier (179-199) uncovered. §11 states scrypt dispatch exists so pre-auth accounts are not locked out — untested. Password/token code at 60% is the weakest-covered security-relevant module. |
| `core/question_bank.py` — **65%** | 65 stmts, 23 missed | medium | The filter-combination branches of `list_questions`/`count_questions` (111-128, 184-201) are uncovered; `tests/test_api/test_questions.py` drives only some filters through HTTP. |
| `core/diagram_shapes.py` — **68%** | 314 stmts, 100 missed | medium | Lines 256-323 and 431-478 — including the `_find_box_by_lines` ruled-paper fallback §7 names as the known hard case. |
| `core/llm.py` — **69%** | 195 stmts, 61 missed | medium | Lines 447-521 and 398-421 uncovered, including retry/rate-limit handling. `tests/conftest.py`'s fake `_generate` bypasses most of the module by design. |
| `core/evaluator.py` — **70%** | 46 stmts, lines 30-33, 40-54 | medium | The embeddings path is behind the `slow` marker, which `test-fast` deselects — so **CI never covers it**. A full `make test` would raise this. |
| `core/booklet_persist.py` — **71%** | 80 stmts, 23 missed | medium | Lines 122-136, 235-280. The module §11 names as the reason region text was unpersisted. |
| `core/booklet_segmenter.py` — **71%** | 196 stmts, 57 missed | medium | Lines 224-262 and 400-436 — the real PaddleOCR `LayoutDetection` path and `detect_question_markers`. |
| **`frontend/` — no tests at all** | `frontend/` (57 tracked files) | high | Zero test or spec files; `package.json` has no test script and no test runner among its devDependencies (`vite`, `typescript`, `oxlint`, `tailwindcss`, …). `.github/workflows/ci.yml` never touches it — no `npm ci`, no `tsc -b`, no `oxlint`. A whole tier of the product is outside CI. |

**Caveat on these numbers.** The run was `-m "not slow"` (12 deselected), matching what
CI actually runs, so sentence-transformer paths are understated. It also produced
**5 failures** — `test_a_platform_admin_gets_the_bypass_context_not_a_college`, three
`test_persist_extractions_*` in `test_booklet_evaluator.py`, and
`test_dry_run_writes_zero_rows_to_evaluation_results`. These are **local environment
drift, not code defects**: `scripts/migrate.py --status` shows 001-019 all applied, but
the fixture answer `a0a0a0a0-0003-0003-0003-a0a0a0a0a0a0` is present in
`migrations/seed_minimal.sql` and absent from the live database. The local DB needs
re-seeding. Reported here for accuracy — resolving it may move the figures slightly.

---

## 6a. Coverage gap ranking (added 2026-09-12, re-run against current `main`)

Re-ran `pytest -m "not slow" --cov=core --cov=api` (same command as §6) to get
current numbers rather than trust the 2026-09-09 table verbatim — it had
already drifted: `core/pattern_tree.py` is no longer 0% (commit `7905a4e`,
"nest slot trees through one two-pass `pattern_tree.nest_slots()`", added
coverage; it's now 52%, lines 29-30/71/74-80/88/112-119 missed), and two
core write/eval-path modules at 0%/10% were not in §6's table at all.
Ranked by centrality (`core/` over `scripts/`, write-paths over read-paths;
`scripts/` is intentionally excluded — see §1/§4, CLI entry points aren't
meant to carry unit-test line coverage the way `core/` functions are):

| Rank | File | Coverage | Why it's ranked here |
|---|---|---|---|
| 1 | `core/reference_assets.py` | **0%** (30/30 missed) | Write path: `insert_reference_asset()` is what both `load_reference_diagram.py` and `load_reference_table.py` write through (§2 already flags them as a duplication pair). Not in the original §6 table at all. |
| 2 | `core/block_evaluation.py` | **10%** (57/63 missed) | Write/eval orchestration: `evaluate_block()` is what both `evaluate_table_answer.py` and `evaluate_diagram_answer.py` call. Also missing from the original §6 table. |
| 3 | `core/users.py` | 60% (49/122 missed) | Security-critical write path (password hashing, legacy-scrypt dispatch, token issuance) — audit already called this "the weakest-covered security-relevant module." |
| 4 | `core/ocr_engines/paddleocr_engine.py` | **0%** (53/53 missed) | The engine that actually reads student handwriting; only the `FallbackOCR` selection layer around it is tested, with fakes. |
| 5 | `core/ocr_engines/tesseract_engine.py` | **0%** (53/53 missed) | Same — the documented graceful-degradation path (missing `tesseract` binary) has no test. |
| 6 | `core/booklet_persist.py` | 71-72% (22/79 missed) | Explicitly "all writes" for booklet ingestion (§7C) — the module that turns segmenter output into `answers`/`answer_blocks` rows. |
| 7 | `core/diagram_extractor.py` | 22% (69/88 missed) | `extract_diagram_structure()`, the §7 headline function, is entirely uncovered by unit tests (only exercised by the offline benchmark scripts, which CI never runs). |
| 8 | `core/db.py` | 52% (12/25 missed) | Foundational — every DB-touching code path depends on `get_connection()`; only `get_admin_connection()` and part of `get_connection()`'s body are uncovered, because every test uses its own fixture connection instead. |
| 9 | `core/storage.py` | 54% (31/67 missed) | The real-MinIO half is untested; `presigned_get_url()` is load-bearing for `GET /results/{id}/pages/{n}/image`. |
| 10 | `core/pattern_tree.py` | 52% (16/33 missed) | Narrowed from 0% by `7905a4e` since the 2026-09-09 audit — worth re-checking before assuming it's still a gap; `nest_slots()` (the two-pass nester used by `paper_generator.py`, `view_pattern.py`, `view_paper.py`) still has uncovered branches. |

Also unchanged from §6 and still worth a look, in rough centrality order:
`core/diagram_shapes.py` (68%), `core/llm.py` (69%), `core/evaluator.py` (70%,
gated behind the `slow` marker CI deselects), `core/booklet_segmenter.py`
(71%), `core/question_bank.py` (65%), `core/paddle_workarounds.py` (55%).

`frontend/` (57 files, no test runner configured, not in CI — see §7) is a
separate, larger gap outside this ranking's centrality scale (it's a whole
other stack, not a `core/`-vs-`scripts/` comparison).

**No tests were written in this pass** — this is a ranked list for a human
decision on what to test next, not a coverage fix.

---

## 7. Outdated docs

`CLAUDE_CONTEXT.md` is a snapshot "as of 2026-09-05" (its own header) and is now **one
full phase behind** — the six commits from `7b379bc` through `a4d067b` built the entire
React frontend and applied migration 019, neither of which the document reflects.

| Item | Location | Confidence | Reasoning |
|---|---|---|---|
| **"Still absent: any frontend"** | `CLAUDE_CONTEXT.md:41`; §2 table row `Frontend \| ReactJS/Angular \| ❌ not started` (`:61`); §3's tree has no `frontend/` | high | `frontend/` holds **57 tracked files** — a complete Vite/React/TS app with `AuthProvider`, `RequireAuth`, `RequireCollegeUser`, a generated typed API client (`src/api/schema.d.ts`), and 9 routes (Login, Upload, Results, ResultDetail, BookletSummaries, QuestionBank, PaperGeneration, Classes, Home). The document contradicts itself: §11's 2026-09-06 CORS note already resolves an unset `CORS_ORIGINS` to "the local Vite dev origin". |
| **"`migrations/019_answer_block_extractions.sql.proposed` … is NOT APPLIED"** | `CLAUDE_CONTEXT.md:1550-1558` (§11) and `:1189-1196` (§10) | high | `migrations/019_answer_block_extractions.sql` exists, is tracked, and `scripts/migrate.py --status` reports it **applied**. `answer_block_extractions` is referenced across 8 files including `core/answer_regions.py`, `core/booklet_evaluator.py`, `api/services/evaluation.py` and `scripts/evaluate_booklet.py`. Commit `a4d067b` is titled "Persist raw per-region OCR extractions". |
| **Whole §11 section "The region text the Results screen needs is NOT PERSISTED"** | `CLAUDE_CONTEXT.md:1522-1558` | high | Its central claim — *"the text a plugin extracts from a scanned region is stored NOWHERE"*, *"there is no `UPDATE answer_blocks` in the repo at all"*, *"a region's `text` is `null` with `text_source: null`"* — was resolved by 019. The section describes a gap that has been closed and even names the `TODO(019)` LEFT JOIN as pending. This is the single most misleading passage in the document. |
| **"the next migration number is 017"** | `CLAUDE_CONTEXT.md:1162` (§10) | high | Internally contradicted by §5 (`:345`), which correctly says 019. Migrations now run to 019, so the next is 020. |
| **§5's migration listing stops at 018** | `CLAUDE_CONTEXT.md:336-374` | high | No entry for `019_answer_block_extractions.sql`. |
| **"Twenty-three endpoints, and that is the complete list in EVERY environment"** | `CLAUDE_CONTEXT.md:1412` and the §11 table at `:1433-1456` | high | The app serves **24**. The table has only **22** rows. Missing: `GET /api/v1/results/booklets` (new in `a4d067b`) and `GET /api/v1/papers/` (which §11's own pagination paragraph at `:1423` lists as existing). Three different counts in one section. |
| **"tests/ … 419 test functions"** | `CLAUDE_CONTEXT.md:178` (§3) | high | 363 `def test_` across `tests/`; 430 collected after parametrize expansion. Matches neither figure. |
| **`ENABLE_DEBUG_ENDPOINTS` — "two comments still say it does"** | `CLAUDE_CONTEXT.md:1241-1247` (§10) | high | Half fixed, half not. `.env.example:84` **has** been corrected ("It no longer registers any endpoint — the whoami probe was deleted"). `api/settings.py:72` **has not** — it still describes `GET /api/v1/_debug/whoami` and cites `api/routers/debug.py`, a file deleted in the 2026-09-05 hardening pass. Fix `settings.py:72`, then narrow this bullet from two comments to zero. |
| **`get_admin_conn` docstring's rationale** | `api/deps/db.py:359-362` | high | *"admin authorization does not exist yet (api/deps/identity.py always reports is_platform_admin=False)"* — untrue since 2026-09-06. `api/deps/identity.py` now resolves a real `platform_admin` role from the JWT `role` claim, and §11 documents `platform_admin → SET LOCAL app.is_platform_admin`. The dependency's stated reason for being unwired no longer holds; see §1. |
| **`core/booklet_summary.py` missing from §3's tree** | `CLAUDE_CONTEXT.md:120-160` | high | New in `a4d067b`, backs `GET /api/v1/results/booklets`. Also missing from §3's `api/` sub-tree: `deps/quota.py`, `deps/pagination.py`, `logging_config.py` (all discussed in §11 prose but absent from the structure listing). |
| **`README.md` — "Redis — job queues (OCR/evaluation jobs) and review locks"** | `README.md:20` | high | Redis was **explicitly rejected** (§11, `migrations/README.md`, `CLAUDE_CONTEXT.md:1166`). The public-facing README advertises the one dependency the architecture argues against. CLAUDE_CONTEXT §10 flags README's *file layout* as stale but not this. |
| **`README.md` — pgvector as a live dependency** | `README.md:18` and `:64` ("PostgreSQL 14+ (with `pgvector` extension for AI evaluation features)") | high | §10 and §2 both state pgvector is named in the design doc and implemented nowhere — no extension, no embedding columns, no index. `README.md:64` is a **setup instruction** telling operators to install an extension the code never uses. |
| **`README.md` — "Answer schema (8 tables)"** | `README.md:53` | high | §5 says 12, of which 9 are RLS-protected, plus `users`/`refresh_tokens` from 017. |
| **`README.md` file tree** | `README.md:25-45` | high | Already flagged in §10 for `db.py`/`storage.py` at root, but it is worse than that: migrations stop at 004 (of 19), only 2 of 31 scripts appear, and there is no `core/`, `api/`, `tests/` or `frontend/`. It also links `PROJECT_CONTEXT.md` at the repo root; the file lives at `readme files/PROJECT_CONTEXT.md`. |
| **§2's "migrations 001–006, 008–015 applied"** | `CLAUDE_CONTEXT.md:49` | high | Now 001-006, 008-019. |
| **`rough_edges.txt` — resolved items still listed as open** | `rough_edges.txt` items 1, 4, 5, 13, 14, 15, 16, 18, 19, 21 | medium | Items 2 and 6 carry inline `FIXED` markers; ten others do not, though `CLAUDE_CONTEXT.md` §10/§11 record every one as closed (auth, list endpoints, `reviewer_id`, `report.json`, CI/container, migration runner, dependency pinning, rate limiting, pagination, structured logging). Gitignored working notes, so low stakes — but it reads as an open defect list. |

### Correctly open — verify before "fixing"

`docs/decisions/reopening-a-finalized-answer.md` and
`docs/decisions/topic-aware-paper-generation.md` (both written 2026-09-06, both gitignored
via `.gitignore:97`) each state **"Status: open"** and **"This brief decides nothing."**
So `CLAUDE_CONTEXT.md` §10 decisions 5 and §7B's topic-matching note remain accurate and
must **not** be marked resolved. The only gap is that `CLAUDE_CONTEXT.md` never mentions
`docs/decisions/` exists.

---

## 8. Needs manual review — NOT cleared for cleanup

**Per the brief, nothing below may be carried into a cleanup prompt without your explicit
sign-off first.**

| Item | Location | Why it is low confidence |
|---|---|---|
| `booklets/` — 28 MB of apparent real student submissions | `booklets/` | Listed at high confidence in §3 as *unreferenced*, but the **action** is low confidence and is not mine to choose. This is student PII in version control. `git rm --cached` does not remove it from history; a history rewrite is a coordination event, not a cleanup task. It may also be deliberate held evidence for a demo. **Decide the policy first; the file operation is the last step, not the first.** |
| `get_admin_conn()` | `api/deps/db.py:341` | Unused, but deliberately so, and documented in four places as the correct dependency for future cross-tenant work. Deleting it would delete a design decision. My recommendation is to fix the stale docstring (§7) and keep the function — but that is a call for you. |
| `--weights` parsing duplication | `scripts/evaluate_answer.py:200-208` ↔ `scripts/evaluate_pending.py:77-85` | 8 lines, mostly `argparse` boilerplate. Extracting it may cost more indirection than the duplication does. |
| Platform-admin connection preamble duplication | `scripts/evaluate_pending.py:87-97` ↔ `scripts/evaluate_pending_diagrams.py:194-204` | §7 explicitly describes the diagram runner as *mirroring* the text one, so this parallelism may be intentional and worth preserving for readability. |
| `dry_run` transaction epilogue at four sites | `core/booklet_evaluator.py:1240`, `core/plugins/persistence.py:173`, `core/booklet_persist.py:274`, `scripts/run_job_worker.py:167` | Textually identical but each sits in a different transaction discipline (`booklet_persist` writes regions, `run_job_worker` writes terminal job state). Merging them couples four independently-reasoned commit boundaries. Read all four in full before touching any. |
| `api/settings.py`'s 11 unread `PG*`/`MINIO_*`/`GROQ_*` fields | `api/settings.py:40-54` | Removing them is safe for behaviour but contradicts the module docstring's stated purpose. The better fix is probably the opposite — make them actually validate at startup — which is a feature, not a cleanup. `pguser`'s `"postgres"` default is the one part I would change regardless. |
| The 5 failing tests | see §6 | Diagnosed as a stale local seed, not a code defect, and I did not re-seed (that writes to the database). Re-run after `scripts/reset_and_seed_db.sh` before drawing any conclusion from them. |

---

## Summary

| Category | Actionable (high/medium) | Needs review (low) |
|---|---|---|
| 1. Dead code | 4 | 1 |
| 2. Duplicated logic | 8 | 3 |
| 3. Stale tracked files | 1 | 1 (same item, policy call) |
| 4. Unreferenced scripts | 6 (all doc fixes, **0 deletions**) | 0 |
| 5. Dead config / env vars | 6 | 1 |
| 6. Test coverage | 15 | 1 |
| 7. Outdated docs | 17 | 0 |

The genuinely dead code is three symbols and one unused test variable — this repo does
not have a dead-code problem. The weight is in **documentation drift** (§7: 17 items,
with `CLAUDE_CONTEXT.md` a full phase stale and `README.md` advertising two rejected
technologies), **duplication that re-formed after RE-7 centralized its first instance**
(§2: the rate limiter and the `FallbackOCR` singleton are the same shape RE-7 fixed), and
**one unexamined 28 MB directory of student PDFs** (§3) that is a privacy decision rather
than a cleanup task.

Suggested order for follow-up sessions: `booklets/` policy decision → §7 doc corrections
(largest volume, zero code risk) → §2 duplication → §1 + §5 code removals → §6 coverage.

**No action was taken on any of the above in this session.**
