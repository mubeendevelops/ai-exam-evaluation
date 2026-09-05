# `api/` — FastAPI layer

HTTP front end over the existing `core/` library. Nothing here reimplements
evaluation logic.

```bash
set -a && source .env && set +a
.venv-paddleocr/bin/uvicorn api.main:app --reload          # http://127.0.0.1:8000/docs
.venv-paddleocr/bin/python scripts/run_job_worker.py --stub   # in another shell
```

## Endpoints

| method | path | |
|---|---|---|
| `POST` | `/api/v1/upload` | multipart booklet PDF → storage → one `booklet_uploads` row. Queues nothing. |
| `POST` | `/api/v1/evaluate` | `{upload_id, exam_id, student_id}` → one queued `booklet_eval` job → 202 |
| `GET` | `/api/v1/jobs/{job_id}` | status, stage progress, error — another college's job is **404** |
| `GET` | `/api/v1/results/{answer_id}` | score, per-signal breakdown, components, confidence, ledger history, reviews |
| `POST` | `/api/v1/results/{answer_id}/override` | teacher override → `answer_reviews`, **never** the ledger |
| `GET` | `/api/v1/questions` | list/filter the shared bank by status, style, source, AI-flag, paper |
| `GET` | `/api/v1/questions/{id}` | one question + its source paragraph + review history |
| `POST` | `/api/v1/questions/generate` | paragraph → N **draft** questions → 201 |
| `POST` | `/api/v1/questions/{id}/review` | **gate 1 (quality)** — draft → confirmed \| rejected |
| `POST` | `/api/v1/questions/{id}/promote` | **gate 2 (publish)** — confirmed → live |
| `POST` | `/api/v1/papers/generate` | pattern → paper filled with **live** questions → 201 |

Eleven endpoints, and that is the complete list in **every** environment —
there are no env-gated routes. `tests/test_api/test_rls_isolation.py` derives
its endpoint matrix from the app's own OpenAPI schema and fails if this table
and the app disagree.

End to end (stub mode — no models, no Groq):

```bash
H='X-Debug-College-Id: 11111111-1111-1111-1111-111111111111'
API=http://127.0.0.1:8000

# 1. store the PDF
UPLOAD=$(curl -s -H "$H" -F file=@media/booklets/sample_booklet.pdf \
         $API/api/v1/upload | python3 -c 'import sys,json;print(json.load(sys.stdin)["upload_id"])')

# 2. queue an evaluation for it  (the booklet must ALREADY be ingested —
#    scripts/ingest_booklet.py; the API does not ingest, see below)
JOB=$(curl -s -H "$H" -H 'Content-Type: application/json' \
       -d "{\"upload_id\":\"$UPLOAD\",\"exam_id\":\"<uuid>\",\"student_id\":\"<uuid>\",\"stub\":true}" \
       $API/api/v1/evaluate | python3 -c 'import sys,json;print(json.load(sys.stdin)["job_id"])')

curl -s -H "$H" $API/api/v1/jobs/$JOB        # -> queued, stage "queued"
python scripts/run_job_worker.py --once --stub
curl -s -H "$H" $API/api/v1/jobs/$JOB        # -> succeeded, stage "done"

# 3. the per-answer report, then a teacher override
curl -s -H "$H" $API/api/v1/results/<answer_id>
curl -s -H "$H" -H 'Content-Type: application/json' \
     -d '{"reviewer_id":"<uuid>","action":"overridden","final_marks":9.0}' \
     $API/api/v1/results/<answer_id>/override
```

## Layout

```
api/
├── main.py         # create_app(): CORS, exception handlers, router mounting
├── settings.py     # pydantic-settings over the SAME .env keys the CLI uses
├── deps/
│   ├── db.py       # get_tenant_conn() / get_admin_conn() — the ONLY DB entry points
│   └── identity.py # STUBBED get_current_user() — replaced wholesale by real auth
├── routers/
│   ├── upload.py     # POST /api/v1/upload
│   ├── evaluation.py # POST /evaluate, GET /results/{id}, POST /results/{id}/override
│   ├── jobs.py       # GET  /api/v1/jobs/{job_id}
│   ├── questions.py  # the bank + the TWO MANDATORY REVIEW GATES
│   └── papers.py     # POST /api/v1/papers/generate
├── schemas/          # upload, evaluation, jobs, questions, papers — pydantic v2
└── services/
    ├── evaluation.py # router <-> core/ adapter. TRANSLATION ONLY, no scoring.
    ├── questions.py  # adapter onto scripts/review_question.py's two gates
    └── papers.py     # adapter onto core/paper_generator.py
```

`core/question_bank.py` holds the one query the question endpoints needed that
did not already exist — a filtered read over the bank. It is in `core/` and not
in `api/services/` for the usual reason: a `--status` flag on
`scripts/review_question.py` should find it already written rather than a
second, subtly-different `SELECT` living in the web layer.

The queue and the upload table are not in `api/`: `core/jobs.py` and
`core/uploads.py` hold the SQL, `scripts/run_job_worker.py` is the worker,
because the API, the worker and (eventually) a CLI all use them and none should
re-derive it.

`api/services/evaluation.py` contains no scoring, aggregation, confidence or
persistence logic and must never grow any. Every number it returns was computed
by `core/booklet_evaluator.py`; every row it writes goes through
`core/plugins/persistence.py` or `core/answer_evaluation.py`. If you are about
to add an `if` there that changes a score, a threshold, or which reference is
used — it belongs in `core/`, where the CLI can reach it too.

## Rule 1 — the API calls `core/`, never `scripts/`

The repo was built as CLI scripts over shared `core/` functions specifically so
an API could call those same functions directly. `scripts/` and `api/` are two
front doors onto one library. Shelling out to a script from an endpoint, or
copying logic out of one, makes them drift — and the CLI stops being a usable
way to debug what the API did.

**The one standing exception, and why it is one.** `api/services/questions.py`
imports `review_question()`, `promote_question()`, `show_question()` and
`generate_questions()` from `scripts/`, because that is where they live — the
two-gate review flow was never moved into `core/`. Both files were written as
plain cursor-taking functions with docstrings saying, explicitly, that a future
API should call them directly rather than shell out, so importing them serves
Rule 1's *purpose* (one implementation, two front doors) while breaking its
letter. The alternative — a second copy of the gate logic inside `api/` — is
precisely the drift the rule exists to prevent, and on this particular logic it
would be the most dangerous copy in the repo.

The clean fix is a mechanical one nobody has done yet: move those four
functions into a `core/` module and leave `scripts/review_question.py` and
`scripts/generate_questions.py` importing them, so the CLI is unchanged and
`api/` stops reaching into a front door. Until then, this exception is the
`scripts/` import that is allowed, and it is the only one.

## Rule 2 — no endpoint opens a raw connection

Every DB-touching endpoint takes `Depends(get_tenant_conn)` or
`Depends(get_admin_conn)`. `grep -rn --include=*.py "get_connection" api/` must only match
`api/deps/db.py`.

This is not style. The answer-schema tables are RLS-protected
(`migrations/003_multi_tenancy.sql`) and their policy uses
`current_setting('app.current_college_id', true)` — the missing_ok form. With
no tenant context set, that is NULL, the policy matches nothing, and **queries
return zero rows instead of raising**. An endpoint that opens its own
connection does not crash; it 404s, or returns `[]`, or writes an orphan row,
and looks entirely healthy while being blind to all of the tenant's data.
There is no exception, no log line, no metric — which is why the discipline has
to be structural rather than remembered.

If you are about to open a connection inside a router because threading the
dependency through is awkward: fix the dependency. `api/deps/db.py`'s module
docstring is the long version of this argument, written for exactly the moment
you are in.

`get_tenant_conn()` also takes the tenant from the **authenticated user only**
— never from a body, query, or path parameter. RLS will faithfully scope a
request to whatever college id you hand it, including one an attacker typed.

## Rule 3 — another tenant's resource is 404, never 403

403 says *this exists, you may not have it*. That turns any id-addressed
endpoint into an existence oracle: a caller can enumerate uuids and learn which
ones name real rows in other colleges, and how many. 404 for both "does not
exist" and "not yours" leaks nothing, at the cost of a less helpful message for
someone who mistyped their own id.

`tests/test_api/test_jobs.py` asserts the two responses are indistinguishable.
Keep extending that test as endpoints are added — the pattern is easy to follow
and equally easy to forget once.

## Rule 4 — identity lives in one file

`api/deps/identity.py` is currently a stub that reads an `X-Debug-College-Id`
header. It is the only file in the codebase that knows that header exists, and
it gets replaced wholesale when real auth is built. Endpoints depend on
`CurrentUser`, never on how it was resolved.

## Known gap — RLS is not enforced when `PGUSER` is a superuser

Postgres exempts superusers and `BYPASSRLS` roles from row-level security.
`FORCE ROW LEVEL SECURITY` (applied in migration 003) closes the table-*owner*
loophole but not the superuser one. With `PGUSER=postgres` — the value in
`.env.example`, so most local setups — **every policy in migration 003 is
inert**: two different `app.current_college_id` values see the same rows.

The API sets the context correctly either way, and
`tests/test_api/test_tenant_context.py` asserts that against the live DB. The
one test that checks isolation *behaviour* skips with a loud message under such
a role rather than pretending to pass.

Before anything resembling production, create a non-superuser application role
that owns nothing and has `NOBYPASSRLS`, grant it DML on the answer schema, and
point `PGUSER` at it. Then that test enforces the isolation for real.

**Until then, the explicit `college_id` predicate is what isolates tenants.**
`core/jobs.py::get_job()` filters on `college_id` in its own `WHERE` clause as
well as relying on the policy. That is not redundancy today — it is the only
mechanism actually running.
`tests/test_api/test_jobs.py::test_isolation_holds_at_the_query_layer_not_only_via_rls`
drives the query under a deliberately wrong RLS context to pin this down, so
"simplifying" the predicate away fails a test instead of quietly removing
isolation on every superuser deployment.

## The append-only ledger — the rule this API must not break

`evaluation_results` is an append-only ledger of what the AI computed
(PROJECT_CONTEXT.md rule 2). **`POST /results/{id}/override` writes only to
`answer_reviews`.** It does not UPDATE the ledger, does not INSERT into it, and
does not flip `is_current`.

Why not just update the score:

* The model really did output that number. Overwriting it erases the evidence
  needed to tell whether the model is systematically wrong — which is the whole
  reason the ledger is append-only.
* Every `evaluation_results` row carries a reference FK (migration 012's XOR)
  and metrics attributing the score to a plugin and version. A human review has
  neither, so an override row there would lie about its own provenance.

The two are reconciled at **read** time by
`api/services/evaluation.py::_final_marks`: the newest override with an explicit
mark wins, and the AI's score is returned beside it. `final_marks` is computed,
never stored — storing it would mean UPDATEing a row whenever a review arrives,
and both source tables are append-only by design.

`tests/test_api/test_evaluation.py::test_override_writes_answer_reviews_and_never_touches_the_ledger`
scores an answer for real, snapshots every ledger row for it **with its
contents**, overrides, and asserts the snapshot is unchanged. A row count alone
would pass an in-place UPDATE.

A re-score is a different thing and is already handled: `record_evaluation()`
flips the previous row's `is_current` and INSERTs a new one. That is the only
sanctioned way the "current" score changes.

## Job queue

`evaluation_jobs` (migration 014), claimed with `SELECT ... FOR UPDATE SKIP
LOCKED`. Redis was rejected — see `migrations/README.md` for the argument.

Worker transaction shape, and the part that is easy to get wrong:

```
txn 1:  claim (status -> running)  COMMIT   <- releases the row lock
        do the work                          <- minutes, no transaction open
txn 2:  mark succeeded / failed    COMMIT
```

The claim is committed *before* the work starts. Holding it open across a
minutes-long evaluation would pin the oldest xmin and block vacuum
database-wide. The `running` status, not the lock, is what marks a job taken.

**The worker runs the real pipeline.** A `booklet_eval` job goes through
`api/services/evaluation.py::run_booklet_evaluation` →
`core/booklet_evaluator.load_booklet_tasks` → `evaluate_booklet` →
`persist_question_results`, appending one ledger row per question.

**Progress** is written to `evaluation_jobs.progress` between phases (migration
015) and surfaced as `{stage, percent, message, counts}`. `percent` is a stage
marker, not a measured fraction — `evaluate_booklet` runs its thread pools with
no progress callback, so within-stage completion is genuinely unknown and is not
invented.

**Ingestion is not part of an evaluation job.** The regions must already exist
as `answer_blocks` rows, from `scripts/ingest_booklet.py`. A booklet with none
fails naming that step rather than succeeding with an empty report that
aggregates to zero and reads like a student who wrote nothing.

**A `--stub` score is FABRICATED and is still appended to the ledger.** Never
point a `--stub` worker at a production queue, and note that `stub` on
`POST /evaluate` is refused with 403 outside development for the same reason.

**No stalled-job reaper yet.** A worker killed between the two transactions
leaves the row `running` forever. `attempts` and `started_at` are on the row for
a future reaper, which needs a heartbeat/lease column to avoid racing a
slow-but-healthy job. Manual lever: `--requeue-stalled MINUTES`.

## The question bank is SHARED — there is no tenant to isolate there

`/api/v1/questions` and `/api/v1/papers` take `get_tenant_conn()` like every
other endpoint, and that connection authenticates the caller and keeps one
connection discipline across the API. It does **not** scope those endpoints to
a college, because there is nothing to scope: the question schema's 12 tables
carry no `college_id` and are not under RLS
(`migrations/003_multi_tenancy.sql` lines 5 and 341), and neither do paper
patterns or generated papers (`006` line 15, `008` line 15). This is a design
decision, stated in those migrations: colleges share one reviewed question
bank.

So `test_questions.py::test_the_bank_is_shared_across_colleges` asserts that a
question IS visible under another college's credential, and
`test_papers.py::test_generation_draws_from_the_shared_bank` asserts the same
for generation. Those tests exist so that tenanting the bank has to break a
test that names where the decision was made — rather than quietly changing what
colleges can see. Rule 3's 404 applies to the answer schema, which really is
tenanted; do not add a `college_id` predicate to the question endpoints to make
them *look* isolated over a column that does not exist.

## Human review is mandatory, and the API has no path around it

A question is answerable by students only at `status='live'`
(`trg_answers_question_must_be_live`), and `live` is reachable only through two
gates, each requiring a named reviewer recorded in `question_status_history`:

```
generate ──> draft ──review(confirm)──> confirmed ──promote──> live
               └────review(reject)───> rejected
```

`POST /questions/generate` writes `draft`, hardcoded, with no request field
that can influence it. `review` fires only from `draft`; `promote` fires only
from `confirmed`. Posting `promote` on a draft is **409 and the row stays a
draft** — proven by `test_promote_cannot_skip_the_review_gate`, which asserts
the database state as well as the status code, because a 409 returned by an
endpoint that had nevertheless promoted the row would be the worst bug
available here.

There is deliberately no combined confirm-and-publish endpoint. It would be two
lines and it would erase the distinction the two gates exist to draw: one says
the content is correct, the other says students may now be asked it.
`test_no_endpoint_moves_a_question_to_live_in_one_step` sweeps every route in
the router with every plausible shortcut body to catch one being added later.

Paper generation is the other half of the same guarantee:
`core/paper_generator.py` selects `WHERE status = 'live'`, so nothing that
skipped a gate can reach an exam paper —
`test_papers.py::test_generation_assigns_only_live_questions` proves it with a
draft and a confirmed question that match the slot perfectly and must both be
passed over.

## Known gap — the API cannot ingest

An uploaded PDF is stored but never segmented into `answer_blocks` rows. That is
still `scripts/ingest_booklet.py`'s job (§7C), so the upload → evaluate loop
works end to end only for a booklet the CLI has already ingested. This is the
largest remaining hole in the wiring; closing it means either an ingestion job
type or an ingest step inside `booklet_eval` (which would rewrite a student's
blocks on every re-score, so it needs thought, not just plumbing).

## Hardening pass — what changed and why

`GET /api/v1/_debug/whoami` **is gone.** `api/routers/debug.py` was deleted.
It was env-gated and it was genuinely useful — it reported the caller's
resolved identity beside the `app.current_college_id` Postgres actually saw in
the request's transaction, which is the one thing a unit test of
`api/deps/db.py` cannot show. But an endpoint that echoes identity and server
session state, kept alive by a boolean, is one mis-set variable away from
production, and `enable_debug_endpoints` is still a live setting because it
gates `stub`/`stub_llm`. Someone turning that on to debug a scoring problem
must not thereby publish an identity probe.

The probe was not lost: it is mounted onto the **test app only**, by
`mount_whoami_probe()` in `tests/test_api/conftest.py`, and
`tests/test_api/test_tenant_context.py` drives it exactly as before. A fixture
cannot be enabled by an environment variable.

**CORS no longer sends credentials to a wildcard origin.**
`allow_credentials=True` with `allow_origins=["*"]` does not mean what it
reads as: Starlette cannot send `Access-Control-Allow-Origin: *` on a
credentialed response, so it echoes the *request's own* Origin instead, with
`Access-Control-Allow-Credentials: true`. The effective policy was therefore
"every origin on the internet may make credentialed requests" — verified
against this app before the fix, with a preflight from `https://evil.example`
coming back allowing `https://evil.example`. Harmless only while identity is
a header no browser attaches automatically; a live hole the day auth becomes
a cookie. `_configure_cors()` in `api/main.py` now enables credentials only
when the origins are actually named, and refuses a config that mixes `*` with
a specific list.

**The catch-all exception handler no longer returns `str(exc)` to the
client.** Unhandled exceptions here are usually psycopg2's, and psycopg2 puts
the failing statement in its message — table names, column names, interpolated
literals. That was a partial schema dump available from any endpoint a caller
could crash. The text still goes to the log always; it goes to the client only
when debug endpoints are enabled.

## The RLS boundary — `tests/test_api/test_rls_isolation.py`

One module asserts the tenant boundary across **every** endpoint at once,
rather than each router checking its own corner. The property:

> A request with no usable tenant context must fail loudly, on every endpoint,
> and must never produce a successful-looking empty result.

Three things make it evidence rather than decoration:

1. **The matrix is derived from the app's OpenAPI schema.**
   `test_every_endpoint_is_covered_by_this_module` fails when the app exposes
   a path the matrix does not exercise — so a router added next month cannot
   quietly skip this. (Verified by adding a route and watching it fail.)
2. **Every request addresses a row that really exists**, and is fired twice:
   once with a credential (must not be 401/403 — proving the request itself is
   good) and once without (must be 401). Without the positive half the whole
   file would pass against an API that rejected everything.
3. **Refusals are asserted to be no-ops.** `test_a_refused_request_writes_nothing`
   snapshots row counts across nine tables before and after firing every
   endpoint uncredentialed.

Verified by mutation: making `get_current_user` default a missing header to a
college — the exact "convenience" this guards against — fails four tests,
including the write check, which reports the rows that were created.

Escalation is covered by eight parametrized cases (invented admin headers,
two ids in one header, tab-based header injection, SQL and `SET`-statement
injection into the GUC, case variation). `SET LOCAL` cannot take a bind
parameter, so `uuid.UUID()` in `api/deps/identity.py` is the only thing
between a header and a SET statement; those cases are what keep it honest.

### Known rough edge — a valid-but-unknown college id

`api/deps/identity.py` only *parses* the uuid; it does not check that the
college exists. A well-formed id for a nonexistent college therefore sets a
tenant context that owns nothing: reads 404 (correct and indistinguishable
from any other miss) but `POST /api/v1/upload` **500s** on the
`booklet_uploads_college_id_fkey` violation. Loud, and it writes nothing — so
the safety property holds — but a 401 naming the unknown tenant would be
better. `test_no_usable_context_is_a_loud_refusal_on_every_endpoint[nil-uuid]`
asserts the current behaviour explicitly rather than smoothing over it.

Fixing it means verifying the college in `get_tenant_conn`, which changes what
every existing cross-tenant test means (they use a nonexistent college id as
"the other tenant" and would all need a real second college). That is a
deliberate change, not a test-file tidy-up, and is left for whoever decides to
make it.
