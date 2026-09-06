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
| `GET` | `/health` | liveness probe — **no credential, no database** |
| `POST` | `/api/v1/auth/login` | email + password → access token + refresh token |
| `POST` | `/api/v1/auth/refresh` | refresh token → a NEW pair; the old one is revoked |
| `POST` | `/api/v1/auth/logout` | revoke a refresh token (`all_sessions` for every one) |
| `GET` | `/api/v1/auth/me` | the account behind the presented token — no DB read |
| `POST` | `/api/v1/upload` | multipart booklet PDF → storage → one `booklet_uploads` row. Queues nothing. |
| `GET` | `/api/v1/uploads` | list/filter this college's uploads by exam-binding state (`bound`) |
| `POST` | `/api/v1/evaluate` | `{upload_id, exam_id, student_id, paper_id}` → one queued job → 202 |
| `GET` | `/api/v1/jobs` | list/filter this college's jobs by status, job_type, created-after |
| `GET` | `/api/v1/jobs/{job_id}` | status, stage progress, error — another college's job is **404** |
| `GET` | `/api/v1/results` | list/filter this college's answers by exam, student, status, needs-review |
| `GET` | `/api/v1/results/{answer_id}` | score, per-signal breakdown, components, confidence, ledger history, reviews, **per-region detail** (page, bbox, classification, flags, reading order) and the merged text that was scored, with each region's offset in it |
| `GET` | `/api/v1/results/{answer_id}/pages/{page_number}/image` | one scanned page as a **300 s presigned URL**, minted per request, stored nowhere. 404 covers "not yours", "no such page" and "no image stored" alike; a dummy-storage ref is 503 |
| `POST` | `/api/v1/results/{answer_id}/override` | teacher override → `answer_reviews`, **never** the ledger |
| `GET` | `/api/v1/exams` | list/filter this college's exams by status |
| `GET` | `/api/v1/students` | list this college's students, optionally filtered to one exam |
| `GET` | `/api/v1/questions` | list/filter the shared bank by status, style, source, AI-flag, paper |
| `GET` | `/api/v1/questions/{id}` | one question + its source paragraph + review history |
| `POST` | `/api/v1/questions/generate` | paragraph → N **draft** questions → 201 |
| `POST` | `/api/v1/questions/{id}/review` | **gate 1 (quality)** — draft → confirmed \| rejected |
| `POST` | `/api/v1/questions/{id}/promote` | **gate 2 (publish)** — confirmed → live |
| `GET` | `/api/v1/papers` | list/filter the shared bank of generated papers by status, pattern |
| `POST` | `/api/v1/papers/generate` | pattern → paper filled with **live** questions → 201 |

Twenty-three endpoints, and that is the complete list in **every** environment —
there are no env-gated routes. `tests/test_api/test_rls_isolation.py` derives
its endpoint matrix from the app's own OpenAPI schema and fails if this table
and the app disagree.

**Every one of them requires `Authorization: Bearer <access token>` except
`GET /health` and the three `/auth` entry points**, and that is asserted the
same way — `test_auth.py::test_no_endpoint_is_reachable_without_a_token`
enumerates the OpenAPI schema and calls each route with no credential. The four
exemptions are listed by name in that test with a reason each; adding a fifth
means editing a test that asks you to justify it.

Endpoints that pass the caller's `college_id` into a query (`/upload`,
`/uploads`, `/evaluate`, `/jobs`, `/results`, `/exams`, `/students`) also
require a COLLEGE-SCOPED role — `teacher` or `admin` — and answer **403** to a
`platform_admin`, who has no college by construction. See
`api/deps/identity.py::require_college_user`; that 403 is about the caller, not
about a resource, so it does not contradict Rule 3 below.

## Pagination

Every list endpoint above shares ONE limit/offset dependency
(`api/deps/pagination.py::get_pagination`) and returns the same shape:
`{items, total, limit, offset}` (`api/schemas/pagination.py::Page`). `total`
ignores limit/offset — it is the count of everything the filters match, so a
client can page without guessing when it has reached the end.

A `limit` above `MAX_LIMIT` (200) is **clamped, not rejected** — a request for
more rows than one page holds is not malformed, and clamping lets `total` do
the talking instead of forcing every such client through a 422/retry cycle.
`GET /api/v1/questions` used to reject with 422 above 200; it now uses the
same shared dependency as everything else. Every `core.<x>.list_*()` clamps
the same `MAX_LIMIT` independently (`core/pagination.py`), so a caller that
reaches one directly, bypassing the API, still cannot pull an unbounded
result set.

Every list query orders by a real timestamp column plus its primary key as a
tiebreaker (e.g. `created_at DESC, job_id DESC`) — required because two rows
written in the same transaction can share a timestamp down to database
precision, and without the tiebreak `LIMIT`/`OFFSET` paging can show one row
twice and skip another.

Nested collections inside a SINGLE-resource response are a different case:
`GET /results/{id}`'s ledger history, review log and per-region components
have no limit/offset request behind them, just an unbounded list embedded in
one report. Those are capped at `core/pagination.py::NESTED_MAX` (50) and
reported as `{items, total, truncated}` (`CappedList`) instead — `total` is
the true count, `truncated` says whether `items` is everything.

`/api/v1/questions` and `/api/v1/papers` are the two SHARED, non-tenanted
lists — see "The question bank is SHARED" below; their list endpoints take
`get_tenant_conn()` to authenticate the caller, and filter on nothing
tenant-shaped because there is no tenant column to filter on.

End to end (stub mode — no models, no Groq):

```bash
API=http://127.0.0.1:8000

# 0. sign in. Create the first account with
#    scripts/bootstrap_platform_admin.py (platform admin), which is the only
#    account not created by another account — there is no open signup.
TOKEN=$(curl -s -H 'Content-Type: application/json' \
        -d '{"email":"teacher@demo.edu","password":"..."}' \
        $API/api/v1/auth/login | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
H="Authorization: Bearer $TOKEN"

# 1. store the PDF
UPLOAD=$(curl -s -H "$H" -F file=@media/booklets/sample_booklet.pdf \
         $API/api/v1/upload | python3 -c 'import sys,json;print(json.load(sys.stdin)["upload_id"])')

# 2. ask for an evaluation. paper_id is required: the booklet's question
#    markers resolve against that paper's slot labels (there is no
#    exams -> generated_papers FK, §7C).
JOB=$(curl -s -H "$H" -H 'Content-Type: application/json' \
       -d "{\"upload_id\":\"$UPLOAD\",\"exam_id\":\"<uuid>\",\"student_id\":\"<uuid>\",\"paper_id\":\"<uuid>\",\"stub\":true}" \
       $API/api/v1/evaluate | python3 -c 'import sys,json;print(json.load(sys.stdin)["job_id"])')

# A booklet with no regions yet gets a booklet_ingest job (job_type says so);
# an already-ingested one gets a booklet_eval job directly.
curl -s -H "$H" $API/api/v1/jobs/$JOB        # -> queued, stage "queued"
python scripts/run_job_worker.py --once --stub   # runs the ingestion
curl -s -H "$H" $API/api/v1/jobs/$JOB        # -> succeeded; result.evaluation_job_id

EVAL=$(curl -s -H "$H" $API/api/v1/jobs/$JOB \
       | python3 -c 'import sys,json;print(json.load(sys.stdin)["result"]["evaluation_job_id"])')
python scripts/run_job_worker.py --once --stub   # runs the evaluation
curl -s -H "$H" $API/api/v1/jobs/$EVAL       # -> succeeded, stage "done"

# 3. the per-answer report, then a teacher override
curl -s -H "$H" $API/api/v1/results/<answer_id>
curl -s -H "$H" -H 'Content-Type: application/json' \
     -d '{"action":"overridden","final_marks":9.0}' \
     $API/api/v1/results/<answer_id>/override
```

## Layout

```
api/
├── main.py         # create_app(): CORS, request-id middleware, exception
│                   #   handlers, router mounting
├── settings.py     # pydantic-settings over the SAME .env keys the CLI uses
├── logging_config.py # JSON log formatting + the request-id contextvar/filter
├── deps/
│   ├── db.py         # get_tenant_conn() / get_admin_conn() / get_auth_conn()
│   │                 #   — the ONLY DB entry points
│   ├── identity.py   # get_current_user(), require_role() — THE only file that
│   │                 #   knows how a caller is identified
│   ├── ratelimit.py  # the login rate limiter (in-process, no Redis)
│   ├── quota.py      # per-college rate limits + the concurrent job-backlog
│   │                 #   cap, for /evaluate, /questions/generate,
│   │                 #   /papers/generate, /upload
│   └── pagination.py # get_pagination() — the ONE shared limit/offset dependency
├── routers/
│   ├── auth.py       # login / refresh / logout / me
│   ├── health.py     # GET /health
│   ├── upload.py     # POST /api/v1/upload, GET /api/v1/uploads
│   ├── evaluation.py # POST /evaluate, GET /results (list) + /results/{id},
│                   #   GET /results/{id}/pages/{n}/image, POST .../override
│   ├── jobs.py       # GET  /api/v1/jobs (list) + /api/v1/jobs/{job_id}
│   ├── questions.py  # the bank + the TWO MANDATORY REVIEW GATES
│   ├── papers.py     # GET /api/v1/papers (list), POST /api/v1/papers/generate
│   ├── exams.py      # GET /api/v1/exams
│   └── students.py   # GET /api/v1/students
├── schemas/          # upload, evaluation, jobs, questions, papers, exams,
│                      #   students, pagination — pydantic v2
└── services/
    ├── evaluation.py # router <-> core/ adapter. TRANSLATION ONLY, no scoring.
    ├── questions.py  # adapter onto scripts/review_question.py's two gates
    └── papers.py     # adapter onto core/paper_generator.py + core/papers.py
```

`core/exams.py`, `core/students.py`, `core/results.py`, `core/answer_regions.py`,
`core/papers.py` (the read side) hold the SQL for the list endpoints that have no real translation
to do — `api/routers/exams.py` and `api/routers/students.py` call them
directly, the same pattern `jobs.py` and `upload.py` already used for
`core/jobs.py`/`core/uploads.py`'s single-row reads. A service-layer adapter
earns its place only where there is real translation or error classification
(questions, papers, evaluation) — see Rule 1 below.

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

## The Results screen's regions, and the text that is not there

`GET /results/{answer_id}` returns, alongside the score, every persisted region
backing the answer in READING ORDER — `page_number`, `region_bbox`,
`block_type`, `classification_label`/`classification_confidence`,
`needs_review` and its ingestion flags, which component scored it and how many
regions were merged into that component, plus any per-region failure. It also
returns `merged_text`: the string §7D actually scored, with each region's
`offset`/`length` inside it, so a teacher can see the SEAM where an answer runs
over a page break. Never the merged string alone. `core/answer_regions.py` owns
the SQL (Rule 1) and the pure join onto `evaluation_results.metrics`.

**A region's `text` is usually `null`, and that is a real finding, not a bug in
this endpoint.** Ingestion writes `answer_blocks.content` as NULL for every
region cut out of a scan, and the OCR output is discarded when the evaluation
run ends — nothing persists it, so nothing can return it. `text_source` is what
distinguishes "no text stored" from "the region is blank", and `merged_text`
refuses to return a partial merge for the same reason. See CLAUDE_CONTEXT.md
§11's "The region text the Results screen needs is NOT PERSISTED" and
`migrations/019_answer_block_extractions.sql.proposed` (drafted, append-only,
**not applied** — its extension keeps `scripts/migrate.py` from seeing it).

`GET /results/{answer_id}/pages/{page_number}/image` is the left-hand panel: a
presigned URL valid for **300 seconds**, minted per request by
`core/storage.py::presigned_get_url` and written nowhere. A URL rather than the
bytes because a 200-DPI deskewed page is multi-megabyte and proxying it would
pin an API worker and its connection per page per reviewer; the cost — the
browser must reach the storage endpoint — is stated in
`api/services/evaluation.py::resolve_page_image`. The page is resolved THROUGH
the answer, so another college's page, a page the booklet does not have, and a
page with no stored image are one indistinguishable 404. A `dummy-storage/`
reference is **503**: it is a placeholder with no object behind it, and
fabricating a URL for it would be a lie.

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

`api/deps/identity.py` is the only file in this codebase that knows how a
caller is identified. Endpoints depend on `CurrentUser`; none of them knows
what a token is, and none may learn.

That rule is what made real authentication a one-file change on 2026-09-06.
The module used to read an `X-Debug-College-Id` header; it now verifies a
signed JWT. Not one router, schema or service changed as a result — the only
edits outside `identity.py`, `db.py` and the new `auth.py`/`ratelimit.py` were
RE-4's (below), which were about a request FIELD rather than about identity
resolution.

### The credential

`Authorization: Bearer <access token>`, a JWT signed with `JWT_SECRET`
(HS256). The claims, and why each is there:

| claim | value | why |
|---|---|---|
| `sub` | `users.user_id` | the account |
| `rid` | `users.reviewer_id` | the ACTOR — every provenance row is attributed to this, and after RE-4 it comes only from here |
| `email` | `users.email` | for `/auth/me` and logs; never a key |
| `role` | `teacher` \| `admin` \| `platform_admin` | drives the session variable, below |
| `cid` | `users.college_id` | **the hinge of tenant isolation**; NULL iff `platform_admin` |
| `typ` | `"access"` | so nothing else this key ever signs can be presented as a login |
| `jti` | random | names a token in a log without logging the token |

### Role → session variable, and that is the whole permission model

`api/deps/db.py::get_tenant_conn` does exactly this, and nothing else:

```
teacher, admin  ->  SET LOCAL app.current_college_id = <cid>
platform_admin  ->  SET LOCAL app.is_platform_admin  = 'true'
```

Both branches read the GUC back and raise `TenantContextError` if it did not
take. `require_role("admin")` exists as a dependency factory for endpoint-level
checks; the one place it is currently applied is `require_college_user`
(= `require_role("teacher", "admin")`), on the endpoints whose queries carry a
`college_id` predicate — see the endpoint table above.

### Two tokens, two jobs

* **Access token** — short (15 min, `ACCESS_TOKEN_TTL_MINUTES`), signed, and
  **not revocable**: `get_current_user` does no database lookup, deliberately,
  so there is no revocation list to consult. Its lifetime IS its blast radius,
  which is why it is short.
* **Refresh token** — opaque random text, long-lived, with a row in
  `refresh_tokens` (migration 017 §2). It is the half that CAN be revoked, so
  it is the half logout acts on, and it is **rotated on every refresh**: the
  presented token is revoked in the same transaction that issues its
  replacement, so a stolen refresh token cannot quietly become a permanent
  second session.

`POST /auth/refresh` RE-READS the account (`auth_user_by_id()`, migration 017
§5) instead of copying the old token's claims forward, so a deactivated or
demoted account stops minting usable access tokens immediately rather than
whenever its refresh token happens to expire.

### Login is the one unauthenticated endpoint that touches the database

It runs on `get_auth_conn()` — **no tenant context and no admin bypass**,
because resolving the tenant is the OUTPUT of authentication, not an input to
it. Setting `app.is_platform_admin` for the login query would hand an
unauthenticated request cross-tenant read on all eleven RLS-protected tables
for the duration of the transaction; leaving `users` unprotected would put
password hashes in the one table with no isolation. Migration 017 §3 argues
this at length and resolves it with SECURITY DEFINER functions that open a
ONE-ROW window each. `test_auth.py::test_the_auth_connection_can_see_nothing_else`
asserts that connection can read no answers, no users, and no refresh tokens.

**Wrong password and unknown email are indistinguishable**, in the response
(one status, one string, no field naming which half failed) and in the TIME
TAKEN — when no user row exists the presented password is verified against a
decoy hash, so both branches pay for exactly one bcrypt verification.
`test_an_unknown_email_costs_the_same_time_as_a_wrong_password` measures it.

Login is rate-limited per client IP and per email address, in process, with no
Redis. `api/deps/ratelimit.py` states plainly what that does and does not buy:
the counters are per-worker and vanish on restart, so a genuinely global limit
belongs in front of the app (nginx `limit_req`, a gateway). What it does buy
completely is that one client cannot mount an unbounded guessing loop through
one worker, and that a flood of bad passwords cannot exhaust this process's CPU
— the check happens BEFORE the bcrypt verification.

### There is no signup endpoint

Accounts are created by an admin. The first one — the platform admin — comes
from `scripts/bootstrap_platform_admin.py`, run by someone with database
credentials, which is the only authority that exists before the first account
does. Open self-registration on a platform where an account IS a tenant
membership would let anyone mint themselves a login; the question that matters
is not "do you own this mailbox" but "which college are you a teacher at",
which only that college can answer.

## Rule 5 (RE-4) — a review's author is the caller, never a request field

`OverrideRequest`, `ReviewRequest` and `PromoteRequest` each used to carry a
`reviewer_id`, because identity knew a college but not a person. That meant any
teacher could attribute a review to any colleague: migration 003's
cross-college trigger refuses reviewers from OTHER colleges, so the whole of
one college's staff was impersonable by any of them.

Those fields are gone. `CurrentUser.reviewer_id` is what the endpoints pass to
the services, `PromoteRequest` no longer exists at all (it had exactly one
field, and a body model with no fields is worse than no body), and
`PaperGenerateRequest.generated_by` went the same way for the same reason. All
four models are `extra="forbid"`, so a client still sending the old field gets
a 422 naming it rather than having its attribution silently replaced.

## RLS is enforced — but only for a non-superuser role (migration 016)

Postgres exempts superusers and `BYPASSRLS` roles from row-level security.
`FORCE ROW LEVEL SECURITY` (applied in migration 003) closes the table-*owner*
loophole but not the superuser one. With `PGUSER=postgres` — the old
`.env.example` value, so every setup — **every policy in migrations 003/014/015
was inert**: two different `app.current_college_id` values saw the same rows.

`migrations/016_application_role.sql` creates the role that was missing:
`ai_eval_app`, `NOSUPERUSER NOBYPASSRLS`, owning nothing, with DML grants only
and no `TRUNCATE`. Its password comes from `APP_DB_PASSWORD` in the
environment, never from the file. `.env.example` now points `PGUSER` at it, and
`PGADMIN_USER` carries the owner credentials that migrations, `pg_dump` and
`scripts/reset_and_seed_db.sh` still need.

    set -a && source .env && set +a
    psql -U "$PGADMIN_USER" -v ON_ERROR_STOP=1 -f migrations/016_application_role.sql

Two tests used to skip under a bypassing role. They now **fail** under one,
because it is a misconfiguration rather than a tolerated state — a skip would
report green while the property is untested:

* `test_rls_isolation.py::test_rls_policies_isolate_tenants` — two real
  colleges, one answer each, one query under two values of
  `app.current_college_id`, two different result sets; plus a connection with
  no context at all seeing zero rows.
* `test_tenant_context.py::test_tenant_connection_is_actually_rls_scoped` —
  the same at the `students` table.

**The explicit `college_id` predicates stay, and are still tested.**
`core/jobs.py::get_job()`, `core/uploads.py` and `api/services/evaluation.py`
filter on `college_id` in their own `WHERE` clauses. That is not redundancy: it
was the *only* mechanism running before 016, and it is the only one left the
moment anyone points `PGUSER` back at a superuser — a one-line `.env` edit with
no visible symptom.
`test_jobs.py::test_isolation_holds_at_the_query_layer_not_only_via_rls` and
`test_rls_isolation.py::test_the_isolating_predicate_is_not_only_rls` now drive
those queries on a **platform-admin connection**, where the permissive bypass
policy makes every tenant's rows visible — so whatever refuses the row can only
be the predicate. Deleting one as "redundant with the policy" fails a test.

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

**Ingestion is a SEPARATE JOB TYPE, not a phase of evaluation.** A
`booklet_ingest` job runs §7C's pass —
`api/services/ingestion.py::run_booklet_ingest` →
`core/booklet_pipeline.ingest_booklet_file` → rasterize, segment, classify,
assign, persist — and then CHAINS into the `booklet_eval` job for the same
booklet, whose id it returns as `result.evaluation_job_id`.

Two job types rather than one because **ingestion writes a student's
`answer_blocks` and evaluation only reads them.** Folding them together would
re-segment on every re-score, and since `insert_region_block` is an
unconditional INSERT that would DOUBLE the regions rather than replace them —
with no version history to recover the first segmentation from
(PROJECT_CONTEXT.md §7 open decision 2). So:

  * `POST /evaluate` on a booklet with no regions → one `booklet_ingest` job
    that chains into the evaluation. `job_type` and `ingest_job_id` in the 202
    say which shape happened.
  * `POST /evaluate` on an ingested booklet → one `booklet_eval` job directly.
    Nothing is re-segmented; a re-score reuses the regions that exist.
  * A re-run of an ingest job (`--requeue-stalled`) skips an already-ingested
    booklet (`result.skipped == "already_ingested"`) and finds the evaluation
    job it queued the first time rather than queueing a second one.

**`booklet_eval` still fails, by name, on a booklet with no regions.** That is
no longer the normal path, but it must never become an empty report that
aggregates to zero and reads like a student who wrote nothing.

**Progress distinguishes the two passes.** An ingest job reports `checking`,
`fetching`, `rasterizing`, `segmenting`, `persisting`; an evaluation job
reports `loading`, `evaluating`, `persisting`. A client seeing `persisting`
reads `job_type` to know whether that means `answer_blocks` or ledger rows.

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

## The API ingests (closed 2026-09-05) — and what it cost

The upload → evaluate loop no longer needs a CLI step. The decision was between
a separate `booklet_ingest` job type and an ingest phase inside `booklet_eval`;
the separate job type was chosen, for the reasons in
`api/services/ingestion.py`'s docstring and in the job-queue section above.
`scripts/ingest_booklet.py` still exists and is still the way to debug a
segmentation — it now imports the same `core/booklet_pipeline.py` the job does.

Two things changed underneath to make it work, and both are worth knowing:

1. **`paper_id` is now a required field on `POST /evaluate`.** Question markers
   ('Q1', 'Q2a') resolve against `pattern_slots.slot_label` through one
   specific generated paper, labels repeat across papers, and there is still no
   FK from `exams` to `generated_papers` (§7C). The CLI's `--paper-id` had the
   same problem and solved it the same way. `exams.paper_id` would close it
   properly and is a §10 product decision, not a migration to write on the way
   past.
2. **Dummy storage now keeps its bytes** — `core/storage.py::dummy_store()` and
   `fetch_to_path()`. `dummy_upload()` does no I/O at all, which was fine while
   nothing read a file back; the ingest job reads the uploaded PDF back in
   another process, minutes later, so the API's upload path stores it under
   `DUMMY_STORAGE_ROOT` (default: a directory in the system temp dir). The ref
   is identical in shape, `dummy_upload()` is unchanged for the loaders that
   populate placeholder rows, and the two processes must agree on
   `STORAGE_MODE` and `DUMMY_STORAGE_ROOT`.

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

## Hardening pass 2026-09-06 — what changed and why

**Per-college rate limiting on `/evaluate`, `/questions/generate`,
`/papers/generate` and `/upload`.** Nothing bounded these before — `core/
llm.py`'s token bucket paces OUTBOUND Groq calls one process makes, not how
many callers ask it to make them. Two limits, both in `api/deps/quota.py`:
a per-minute `SlidingWindowLimiter` (in-process, no Redis — the same
mechanism `api/deps/ratelimit.py`'s login limiter already uses, generalized
to count every accepted call rather than only failures), keyed by the
caller's college and applied via a route-level dependency; and
`check_concurrent_job_cap()`, called inside `POST /evaluate` itself, which
refuses a new job once the calling college already has
`MAX_QUEUED_JOBS_PER_COLLEGE` of its own `queued`/`running` in
`evaluation_jobs`. The second one is what actually protects the shared Groq
daily quota — it is read live from the job table, so it is correct across
every API process and worker, unlike the per-minute limiter.

**`CORS_ORIGINS` has no permissive default outside development.** It used
to silently become `"*"` when unset, in every environment. Unset now resolves
to the local Vite dev origin ONLY when `API_ENV=development`
(`Settings.cors_origin_list`), and raises at startup everywhere else — the
`_configure_cors()` logic from the previous hardening pass (credentials only
with named origins; refuse `*` mixed with a named list) is unchanged.

**Structured JSON logs and a request id on every response.**
`api/logging_config.py` + a request-id middleware in `api/main.py` bind one
id per request (inbound `X-Request-ID` if the caller sent one, else a fresh
uuid4), echo it back as a response header, and stamp it onto every log line
via a `contextvars`-based filter. The catch-all handler's body now carries
`request_id` — the safe correlation handle to the full exception text, which
still never reaches the client. `api/services/ingestion.py`/`evaluation.py`
write the same id into a job's payload so `scripts/run_job_worker.py`'s log
lines for that job (and the `booklet_eval` job an ingest job chains into)
correlate back to the HTTP request that queued them.

**`POST /papers/generate` surfaces an incomplete paper without prose
parsing.** `warnings` is now `list[SlotWarning]` (`slot_label`, `section`,
`marks`, `reason`) instead of formatted sentences, and the response also
carries `is_complete: bool` and `filled_marks`/`pattern_total_marks: float` —
a client can check completeness, or the size of the gap in marks, without
reading `warnings` at all.

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

### A valid-but-unknown college id is a 401 (it used to be a 500)

`api/deps/identity.py` only *validates the claim*. It cannot do more — checking
a college exists needs a database connection — so a token naming a
nonexistent college would establish a tenant context that owns nothing: reads
404ed (correct, and indistinguishable from any other miss) while
`POST /api/v1/upload` **500ed** on the `booklet_uploads_college_id_fkey`
violation. Loud, and it wrote nothing, so the safety property held; it was
still the wrong answer to "who are you?", and it made upload the one endpoint
where an unknown tenant behaved differently from every other.

`api/deps/db.py::get_tenant_conn` now runs **one SELECT against `colleges`
before `SET LOCAL`** (`_require_known_college`). Unknown → 401. Not `active`
(migration 003's `college_status` is `('active','suspended')`) → 401, for the
same reason: a suspended college still owns rows, and admitting it would hand
out a working tenant context for a tenant the platform has deliberately
switched off. `colleges` carries no `college_id` column and no policy, so the
read is correct with or without a context; doing it first means a rejected
request never establishes one. The 401's wording lives in
`api/deps/identity.py::unknown_tenant_error()`, so Rule 4 holds — the
vocabulary of "who are you and why were you refused" stays in one file.

**This changed what every cross-tenant test means, which is why the tests
changed with it.** They all used a nonexistent college as "the other tenant";
against a verified `get_tenant_conn` that is a 401 at the edge, so a "404 for
the other tenant" assertion would have been proving that an unknown caller is
rejected — and would have kept passing with tenant isolation entirely removed.
`seed_minimal.sql` now seeds a second REAL college (`22222222-…`, with its own
student and exam) and a suspended one (`33333333-…`); `tests/test_api/
conftest.py` exposes them as `COLLEGE_B` / `COLLEGE_SUSPENDED` with fixtures
that guarantee the rows exist. Where it is cheap, the cross-tenant tests now
also assert that the SAME credential succeeds on college B's OWN row — which
is what makes the 404 evidence of isolation rather than of rejection.
`COLLEGE_ABSENT` still exists, but only where the 401 itself is the property
under test.
