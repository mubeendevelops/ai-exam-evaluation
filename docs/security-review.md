# Tarn AI Evaluation — Security review (P21)

7 Oct 2026 · scope: the code at the end of P20, the dev stack (`make up`), the Terraform in
`deploy/gcp/` (validated only, never applied). References: `docs/design.md` "Data model and tenancy",
`docs/requirements.md` R7 and decisions 3, 6, 8, `CLAUDE.md` architecture rules 3–6.

## How the review was done

1. Read every API route, the per-request unit of work, the auth services and adapters, the
   upload and page-splitting path, the page sources, the blob stores, the logging set-up, the
   nginx config and the Terraform IAM, storage, load balancer and secrets.
2. Wrote tests that attack each rule, made each new safeguard fail on purpose (a *mutation
   check*: weaken the rule, watch a test fail, revert), and ran the whole suite.
3. Probed the running dev stack by hand: cross-college HTTP calls, crafted SQL as `tarn_app`,
   the access log, and a restore drill from an encrypted identity backup.

Severity: **High** = data of one college reachable by another, or a way to take accounts or the
service down cheaply; **Medium** = a protection missing in depth, or a leak of personal data to
a place it should not go; **Low** = hardening.

## Findings and fixes

| # | Area | Finding | Sev. | Fix | Evidence |
| --- | --- | --- | --- | --- | --- |
| F1 | Tenancy | The blob store was not bound to a college. Keys only come from RLS-scoped rows, but nothing refused a `college/{B}/…` key inside college A's transaction | Medium | `CollegeScopedBlobStore` (core) wraps the store in every `PostgresSession` and in the API's in-memory scope. Another college's key raises `TenantViolationError` (404); `global/…` stays shared; a session with no college reaches `global/` only | `core/tests/test_security_p21.py` (blob tests); mutation checked |
| F2 | Data | The uvicorn access log printed full query strings: `/students?q=<name>` and `/evaluated-booklets?student=&usn=` put student names and USNs in the log. The Terraform load balancer logged 20 % of request URLs | Medium | `DropQueryString` filter on `uvicorn.access`. Terraform: LB request log off for the API, and a Cloud Logging exclusion for Cloud Run request lines with `?` (`logging.tf`) | `test_the_access_log_drops_query_strings`; live: a search for a marker string left no trace in `docker compose logs api` |
| F3 | Data | The data bucket had object versioning: a booklet the teacher deleted stayed as non-current versions for 30 days (+7 soft delete) | Medium | Versioning off. The 7-day soft delete is kept as the operators' undo window, the same as Cloud SQL PITR (D149) | `deploy/gcp/storage.tf`; `terraform validate` |
| F4 | Web | nginx sent **none** of its security headers: an `add_header` inside a `location` drops the server-level ones | Medium | `frontend/security-headers.conf` included in each location: nosniff, `X-Frame-Options DENY`, `Referrer-Policy no-referrer`, COOP, Permissions-Policy, a CSP (`default-src 'self'`, no inline scripts, `img-src 'self' blob: data:`, `frame-ancestors 'none'`) and HSTS | `curl -I` on the nginx image shows them; the built app loaded under the CSP with no violations (headless Chromium) |
| F5 | API | The API sent no security headers | Low | `SecurityHeaders` middleware: nosniff, `DENY`, no referrer, `Cache-Control: no-store` unless the route sets one, CORP, CSP `default-src 'none'` (not on `/docs`), HSTS in production | `test_every_api_response_carries_the_security_headers`, `test_production_adds_hsts`; live `curl -I` |
| F6 | Web | Sign-out kept the TanStack Query cache (booklets, students, marks) in the tab, so the next user of a shared computer, maybe from another college, could briefly see it | Low | `queryClient.clear()` on sign-out, on a lost session (another tab, a refused refresh) and on sign-in | vitest `signs out: forgets every cached answer…`; mutation checked |
| F7 | Auth | No rate limiting (O12/O17): unlimited guesses across accounts, Institution-ID enumeration through the availability check, reset-mail flooding, free registrations | High | `tarn_api.ratelimit`: fixed windows per client address (login 60/5 min, recovery and forgot 20/15 min, token links 30/15 min, availability 30/5 min, registration 5/h) and per (institution, email) (login 10, recovery 5, forgot 3 per 15 min), counted whether the account exists or not; 429 with `Retry-After` and one message. `TARN_TRUSTED_PROXY_HOPS` for `X-Forwarded-For` behind the LB. Cloud Armor rate-based ban in front (`armor.tf`) | `api/tests/test_api_security.py` (8 tests); mutation checked |
| F8 | Auth | Timing told accounts apart: a locked account answered without hashing; recovery for an unknown email answered at once while a real one ran up to 10 argon2 checks and stopped at the matching code | Medium | Same work on every path: a dummy hash for a locked sign-in; recovery always checks every unused code and pads to 10 hashes (unknown, locked and known accounts alike) | `test_a_locked_account_still_does_the_hashing_work`, `test_recovery_costs_the_same_…`; mutation checked |
| F9 | Keys | The pepper could not be rotated (O13): hashes carry no pepper id | Medium | `Argon2Hasher(current, previous=…)` tries the current pepper, then earlier ones; sign-in re-hashes a password still on an earlier pepper (`PasswordHasher.pepper_is_current`). Hash format unchanged (the boss's schema pins `$argon2id$`). `TARN_PASSWORD_PEPPER_PREVIOUS` / secret `tarn-password-pepper-previous`; runbook in `deploy/gcp/README.md` | `test_a_rotation_accepts_the_old_pepper…`, `test_sign_in_moves_a_hash_to_the_new_pepper`, `…recovery_codes_keep_working…`, `…removed_pepper_must_reset` |
| F10 | Config | Production accepted the pepper and signing key from environment variables, an `http://` public URL (emailed tokens), and database URLs without TLS; the signing key had no minimum length | Medium | Production now refuses `TARN_SECRETS_BACKEND≠gcp`, a non-https `TARN_PUBLIC_URL`, and app/identity URLs without `sslmode=require` (or `verify-*`, or a socket); every API refuses a signing key under 32 bytes | `test_production_refuses_development_secrets_and_keys` and the other production-settings tests |
| F11 | Auth | No way to suspend a college, and a suspension would not have ended live sessions (access tokens stay valid until refresh) | Medium | `tarn tenants suspend\|resume ID --operator NAME`: status SUSPENDED, every session of the college revoked (the next request gets 401 because `current_user` checks the session); audited `tenant.suspended` / `tenant.resumed` | `test_suspension_ends_every_session…` (core), `test_a_suspended_colleges_access_token_stops…` (HTTP) |
| F12 | Input | Image decompression bombs: every decoder (`cv2.imdecode` in the cleaner, PDF splitter, OCR, sheet thumbnails) decoded first and measured after, so a 33-byte PNG declaring 60 000 × 60 000 px could allocate gigabytes. PDF images were extracted (inflated) before any size check | High | `tarn_core.services.images` reads PNG `IHDR` / JPEG `SOFn` without decoding. Uploads (booklets, key files, reference diagrams) refuse more than 120 Mpx (413); every decoder refuses before decoding; the PDF splitter checks each image's declared size from `get_image_info` before `extract_image` (MuPDF itself refuses ≥ ~400 Mpx) | `test_a_decompression_bomb_is_refused…`, `test_a_pdf_image_declaring_billions…`, `test_the_cleaner_refuses_a_png_bomb…`; mutation checked |
| F13 | Input | No hard cap on request bodies: Starlette spools a multipart upload whole before the route's own check runs, and `request.body()` (key files, diagrams, roster CSV) reads any length, so a chunked request without Content-Length had no limit (O39, O98) | High | `BodyLimit` ASGI middleware counts bytes as they arrive: booklet upload `upload_max_bytes` + 1 MiB, key files 11 MiB, diagrams 6 MiB, everything else 2 MiB; a declared Content-Length over the cap is refused before reading. 413 | `test_a_declared_oversized_body…`, `test_a_chunked_body_is_cut_off_at_the_cap`, `test_booklet_uploads_get_their_own_cap`; live: 3 MB login body → 413; mutation checked |
| F14 | Input | Tar stream source (`tarn evaluate -`): a GNU sparse member declaring 8 GiB of "real size" expands in `extractfile().read()` far beyond the archive | Medium | Sparse members are skipped; the members' declared sizes together must stay under the stream cap | `test_a_sparse_tar_member_is_skipped…`, `test_tar_members_larger_than_the_cap…` |
| F15 | Input | `GcsEventPageSource` trusted the bucket named in the event and downloaded any number and size of files | Low | Optional expected bucket (an event for another bucket is refused); at most 40 files and 100 MB, judged from the listing before anything is downloaded, and again while downloading | `test_the_event_source_refuses_another_bucket_and_oversized_folders` |
| F16 | Tenancy | O7: `tarn_app` can call `set_config('app.college_id', …)` itself, so RLS holds only while no statement can be injected | Accepted (D147) | Kept by user decision. A static test fails if runtime code passes an f-string, `%`, `+` or `.format` string to `text()`/`execute()`/`exec_driver_sql()`; an integration test documents that the role *can* switch college, so nobody mistakes RLS for a guard against injection | `adapters/tests/test_sql_hygiene.py` (the scan catches 4 kinds of formatted SQL; mutation checked), `test_o7_the_application_role_can_still_name_another_college` |
| F17 | Repo | Nothing stopped a forced `git add` of `samples/`, `var/` or `.env` | Low | CI step: `test -z "$(git ls-files samples var .env)"`. `git log --all -- samples var .env` is empty today | `.github/workflows/ci.yml` |

Also observed, not changed: the development `.env` has `TARN_LLM_SCORER_ENABLED=true` with
the development allowance and keys, and the demo college `DEMO_ENG` has `llm_scoring` on (left
from the P19 live call). DEMO_ENG holds no booklets, so nothing is sent today. Switch it off with
`tarn tenants llm DEMO_ENG --off --operator <name>` unless a test needs it. Cloud OCR is off
(`TARN_CLOUD_OCR_ENABLED=false`).

## 1. Tenancy

**What holds.** Every request opens one transaction per college, with `app.college_id` set from
the signed-in user (who is reloaded on each request). Every college table forces RLS, and the
application role has no superuser or BYPASSRLS and owns nothing (P3/P4 tests). Every repository
call takes `college_id`. Another college's row raises `TenantViolationError` and answers 404,
exactly like a missing row.

**Sweep of every endpoint.** `tarn_api.tenancy_sweep` classifies all 78 operations of the
published OpenAPI document. A test fails when a new operation is not classified.

- College B calls each of the 29 college-data operations with college A's ids (booklet, page,
  region, segment, answer, sheet version, account).
- B tries the 11 global-content writes on content A owns.
- B uses the 8 shared reads and copies.

Expected and found: 404 with `{"detail": "Not found."}` for college data; 403 for writes to
another college's content; 200/201 for reads and "copy to my college". After every call, A's
audit log and A's review of the booklet are unchanged. B cannot register a booklet for A's
student (404). B's lists (booklets, evaluated, student search by A's names, accounts) never
show A's ids. The sweep runs on the in-memory adapters (default suite) and on PostgreSQL +
MinIO as `tarn_app` (integration).

**Direct SQL as `tarn_app`** (live, dev database, ENG session):

| Attempt | Result |
| --- | --- |
| Read DEMO_COM's booklets, students, audit rows | 0 rows |
| `UPDATE` / `DELETE` DEMO_COM rows | `UPDATE 0`, `DELETE 0` |
| No setting at all | 0 rows |
| `SET LOCAL row_security = off` | refused by the RLS policy |
| `SET ROLE tarn` | permission denied |
| `UPDATE` / `DELETE` `audit_events` | permission denied |

The P3/P4 integration tests keep covering every table this way, and the new journey test adds
UPDATE, DELETE and TRUNCATE attempts on `audit_events` and `deletion_records`.

**Global content.** Only the owning college writes it (`ensure_can_edit` in the services, plus
RLS policies at the database). Other colleges read it and copy it. The sweep checks all 11 write
operations, and a mutation that disabled the owner check failed 11 sweep tests.

## 2. Authentication

| Topic | State after P21 |
| --- | --- |
| Tokens | HS256 access token, 15 min, signing key ≥ 32 bytes from Secret Manager. The role is never trusted from the token, and every request reloads the user and checks the session (so logout, disable, force reset and suspension take effect at the next request). Refresh token: opaque, HttpOnly, `SameSite=Strict`, path `/api/v1/auth`, `Secure` in production, rotated on every use; a replayed old token revokes the session. The access token lives only in memory in the browser |
| Password policy | NIST style: at least 12 characters (per tenant 8–128), at most 256, not the email, its local part or the Institution ID, no composition rules |
| Breached passwords | Exact match against the NCSC top-100k list, offline (D43). No online lookup, so nothing leaves the machine (kept; see open items) |
| Lockout | 5 wrong passwords or recovery codes → 15 min lockout per account (persisted). An admin can unlock, and a reset link clears the lockout. The per-account rate limits (F7) also cover unknown and locked accounts |
| Rate limits | F7. Each API instance counts on its own; Cloud Armor counts across instances |
| Reset links / invitations / email verification | 256-bit random, only the SHA-256 is stored, single use (an atomic `use_action_token`; 8 racing uses → 1 wins, P4 test), 30 min / 72 h / 24 h; a newer reset link cancels the older; a link of college A is useless in B |
| Recovery codes | 10 × 80 bits, argon2id with the pepper, each usable once (atomic), a wrong code counts towards the lockout, constant work (F8) |
| Registration | Pending until the email is verified and a Tarn operator approves (`tarn tenants approve`, no HTTP route); 5 per hour per address |
| Admin-only routes | `/accounts*` need `Role.ADMIN` (403 for teachers, checked live). Admins manage teachers only, never other admins |
| Key management | Each tenant has a data key wrapped by Cloud KMS (`gcp-kms:`; `local:` refused in production). Pepper, signing key and SMTP password come from Secret Manager, and production now refuses environment-variable secrets (F10). Pepper rotation: F9 |

**Restore drill** (live, 7 Oct 2026):

1. Issued 10 recovery codes for a synthetic DEMO_COM teacher on the dev API.
2. Ran `tarn identity export`: 4 bundles in 2.0 s, mode 600. In the DEMO_COM bundle (7.4 kB)
   no email, name or hash can be read.
3. Created a fresh identity database `tarn_identity_drill`, ran `tarn identity upgrade` and
   `app-login`, then `tarn identity import <DEMO_COM bundle>`: "restored DEMO_COM: 3
   identities". Steps 3 took 5.9 s.
4. Started an API against the restored identity database, with the same application database:
   - the teacher signed in (200) and saw the college's booklet;
   - a restored recovery code set a new password (204); the same code again was refused (401);
   - the new password signed in (200) and the old one was refused (401);
   - DEMO_ENG, which was not imported, could not sign in (401).
5. The drill API's log held no name, email or password. The drill database was dropped
   afterwards.

Sessions and emailed links are not restored by design: everyone signs in again.

## 3. Data

- **In transit.**
  - Production: TLS at the load balancer (managed certificate, HTTP → HTTPS redirect), HSTS
    from nginx and the API. Cloud SQL accepts TLS only (`ENCRYPTED_ONLY`), and the settings
    now refuse database URLs without `sslmode=require`. SMTP uses STARTTLS or TLS. Cloud Run
    egress is private ranges only.
  - Development: plain HTTP, every port bound to 127.0.0.1.
- **At rest.**
  - Cloud SQL and Cloud Storage use Google-managed encryption.
  - Identity records hold argon2id hashes and data encrypted with the tenant's KMS-wrapped
    data key; identity backups are encrypted the same way.
  - CMEK for Cloud SQL and the buckets is not configured (O105, kept open).
- **Blob keys per college.**
  - `BlobKey` refuses `..`, empty parts and any prefix other than `college/<uuid>/` or
    `global/`.
  - File names are cleaned to `[A-Za-z0-9._-]`, at most 100 characters.
  - Each college session can reach only its own prefix (F1).
  - One bucket and one service account per workload: per-college IAM is not possible at that
    scale, so the application enforces it.
- **Signed URLs.**
  - None are issued. Every page image, key file, diagram and sheet PDF streams through the API
    after authentication, with `Cache-Control: private, no-store` (page images) or `no-store`
    (default).
  - Direct-to-bucket uploads (O39/O98) will need V4 signed URLs of at most 15 minutes, limited
    to one object under the caller's college prefix: recorded as a requirement on that work.
- **No student data in logs.**
  - The access log loses query strings (F2).
  - Structured logs carry ids and counts only (reviewed: worker, stages, LLM, mailer).
  - Error messages never echo row values.
  - The end-to-end journey's audit log was scanned for every seeded student name and USN, the
    answer text the OCR read, the password, `token=` and `$argon2`: none found (in memory and
    on PostgreSQL).
- **Cloud engines off by default.**
  - Textract, Azure, Document AI and the LLM need explicit settings, plus a development
    allowance outside production (D78, D131); the LLM also needs a per-college flag.
  - Unchanged and covered by existing tests; see the observation about the dev `.env` above.
- **`samples/` never committed.** Git-ignored, absent from all history, and now guarded in CI
  (F17).

## 4. Audit

- **Every state change and edit produces an event.** The journey checks the expected event
  after each write: upload, open/lock, approve answer (with override, tags and remarks), approve
  booklet with the sheet issued, amendment opened, amendment approved with sheet v2, close, and
  delete. The pipeline's events are checked too: processed, text read, segmented, scored. Edits
  outside the journey (region text, segments, graphs, content, accounts, auth) are covered by
  the P4/P15 tests (`core/tests/test_workflow_audit.py`, `test_auth.py`).
- **Mutation check.** Dropping the `answer.approved` event failed the journey.
- **Immutable.**
  - `tarn_app` has INSERT and SELECT on `audit_events` and `deletion_records`.
  - UPDATE, DELETE and TRUNCATE are refused, for the owner too, by trigger (P3 tests, the new
    PostgreSQL journey test, and the live probe).
  - New in P21: `tenant.suspended` and `tenant.resumed`.
- **Deletion.**
  - A deleted booklet leaves no row in any table and no object under its prefix.
  - It leaves one `deletion_records` row and one `booklet.deleted` event.
  - Its earlier events stay as content-free markers: action, actor, time and ids remain, and
    every `before`/`after` is NULL. This is the in-place redaction decided in D32, checked on
    PostgreSQL by the journey.
  - Read against the prompt's "deletion leaves only the deletion record": no content of the
    booklet remains, but the event *markers* do, as D32 decided.

## 5. Input handling

| Input | Limits now |
| --- | --- |
| Request bodies | Hard caps while streaming (F13): uploads 30 MiB (Terraform) / 100 MiB (default) + 1 MiB, key files 10 + 1 MiB, diagrams 5 + 1 MiB, others 2 MiB |
| Booklet files | Judged by first bytes (PDF/JPEG/PNG), at most 40 images or one PDF of at most 40 pages (60 in the splitter), images at most 120 Mpx by header (F12) |
| PDFs | Encrypted, password-protected or repaired PDFs refused. Embedded images checked by declared size before inflation, rendered pages capped at 2200 px |
| Roster CSV | 1 MB, 5000 rows, UTF-8, all-or-nothing (P4) |
| Tar streams | No sparse or non-regular members, at most 200 files, declared sizes within the cap, nothing written to disk (F14) |
| Storage events | Expected bucket, at most 40 files / 100 MB, `incoming/<uuid>/<uuid>/_complete` only (F15). Only the API and worker service accounts can write to the bucket |
| Blob keys | Built only from ids and cleaned names; `BlobKey` refuses traversal (`core/tests/test_security_p21.py`, 7 malformed shapes) |
| Searches | `LIKE` patterns escaped (`autoescape`, `_like_escape`), query lengths capped |

Remaining: a PDF whose *vector* content is costly to render. It is bounded by the page cap and
the job lease, and the API never renders. Recorded as open.

## 6. End-to-end tests (5 queued booklets)

`tarn_api.journey` runs the whole teacher journey over HTTP:

1. One teacher queues five booklets; the sixth gets 429 and a duplicate of the same file gets
   409.
2. The worker drains the queue one booklet at a time to `scored`.
3. Each booklet: open (lock) → approve every answer (one override with a tag and a remark) →
   approve the booklet → download sheet v1.
4. A second teacher meets the lock (423).
5. One booklet is amended → sheet v2, v1 kept.
6. The evaluated list shows all five, the amended one `approved_amended`.
7. One booklet is deleted.

Every write is checked for its audit event, and the audit log for secrets and student text.

- `api/tests/test_e2e_journey.py` runs it on the in-memory adapters (default suite).
- `api/tests/test_e2e_postgres.py` runs it on PostgreSQL + MinIO with the worker's
  `BookletJobRunner` on the real queue (scripted OCR), then checks redaction and that no blob is
  left.
- The browser side: `frontend/e2e/evaluate.spec.ts` "five booklets fill the queue" (Playwright,
  API stubbed): five drops fill the queue (`Queue (5 of 5)`), then the drop zone closes with
  "You already have 5 booklets waiting".

## Tests and checks run

- Backend unit 1321 (+105), integration 146 (+52), vitest 250 (+1), Playwright 12 (+1);
  `make lint`, `mypy --strict`, import-linter, the OpenAPI check and `terraform validate` pass.
  `make test-models` (real engines) was run after the decoder changes.
- Mutation checks, each failed a test before it was reverted:
  - blob scope;
  - rate limit;
  - body cap;
  - cleaner pixel check;
  - owner check (11 sweep failures);
  - memory repository ignoring the college (22 sweep failures);
  - locked-path dummy hash;
  - access-log filter;
  - formatted SQL in a repository;
  - the `answer.approved` audit event;
  - the cache clear on sign-out.

## Accepted risks and open items

- **O7 (accepted, D147).** The application role can choose its college in SQL. Mitigated by
  parameterised SQL everywhere (now tested statically). A signed scope (HMAC checked by a
  SECURITY DEFINER function) is possible later, at a per-row cost.
- **Rate limits per instance (D148).** Each Cloud Run instance counts on its own; Cloud Armor is
  the cross-instance limit. The `X-Forwarded-For` layout (`TRUSTED_PROXY_HOPS=2`) has not been
  seen on a deployment (O102).
- **Lockout as denial of service.** Anyone who knows an email and Institution ID can lock that
  account for 15 minutes. A reset link or an admin unlocks it.
- **Breached-password list.** The list holds 100k passwords and is checked offline. An online
  k-anonymity lookup would send hash prefixes to a third party, so it was not added.
- **Uploads held in memory** up to the cap (O39) and Cloud Run's 32 MiB request limit (O98).
  Direct-to-bucket signed uploads remain the fix.
- **Not done in Terraform (O105).** CMEK, uptime checks and alerts.
- **Not built.** An HTTP route for operator actions (approve, suspend, LLM flag) against
  production (O103); only the CLI exists.
- **PDF vector-render cost.** See section 5.
