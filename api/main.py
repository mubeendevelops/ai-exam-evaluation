"""api/main.py — FastAPI app factory.

Run locally:
    set -a && source .env && set +a
    .venv-paddleocr/bin/uvicorn api.main:app --reload
    # then open http://127.0.0.1:8000/docs

The app is built by create_app() rather than at import time so tests can build
independent instances with different settings (e.g. debug endpoints on/off)
without process-global state. `app` at the bottom is the ASGI entry point
uvicorn imports.

Every router mounted here gets its DB connection from api/deps/db.py — read
that file's header before writing one.

NO DEBUG ENDPOINTS ARE MOUNTED, in any environment. There used to be a
`GET /api/v1/_debug/whoami` probe that reported the caller's resolved identity
and the `app.current_college_id` its transaction had actually established. It
was env-gated and it was useful, but "an endpoint that echoes your identity
and the server's session state, disabled by a setting" is exactly the kind of
thing that survives to production behind a mis-set variable — and this build
is heading toward real auth. It was deleted in the Day 5 hardening pass.

The capability it provided was not lost: the same probe is now mounted ONTO
THE TEST APP ONLY, by the `whoami_app` fixture in tests/test_api/conftest.py,
so tests/test_api/test_tenant_context.py still proves the request →
dependency → transaction chain from outside the process. A test fixture
cannot be enabled in production by an environment variable.

`Settings.debug_endpoints_enabled` still exists and still matters: it gates
the `stub`/`stub_llm` request fields on /evaluate and /questions/generate,
both of which write fabricated data into real tables.

AUTHENTICATION (2026-09-06). Every route mounted here requires a bearer
access token except `GET /health` and `POST /api/v1/auth/login`. That is not
enforced router by router: it is enforced by `get_tenant_conn` /
`get_current_user`, which every other endpoint depends on, and asserted over
the app's own OpenAPI schema by
tests/test_api/test_auth.py::test_no_endpoint_is_reachable_without_a_token —
so a new router cannot quietly join the exempt list. See api/deps/identity.py
for what is in a token and api/routers/auth.py for how one is obtained.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.deps.db import TenantContextError
from api.deps.ratelimit import LoginRateLimiter
from api.settings import Settings, get_settings, load_dotenv_once

log = logging.getLogger("api")


def create_app(settings: Settings | None = None) -> FastAPI:
    load_dotenv_once()
    settings = settings or get_settings()

    app = FastAPI(
        title="AI Exam Evaluation API",
        version="0.1.0",
        description=(
            "HTTP layer over the core/ evaluation library. Every DB-touching "
            "endpoint runs inside an RLS tenant context — see api/deps/db.py."
        ),
    )
    app.state.settings = settings

    # Fails the BOOT, not the first login, if JWT_SECRET is missing outside
    # development. A signing key resolved lazily per request would turn a
    # deployment misconfiguration into a 500 on the one endpoint nobody can
    # work around, discovered by a user rather than by the deploy.
    settings.jwt_signing_key

    # One limiter per app instance, holding the failed-login counters. On
    # app.state rather than module-global so two apps in one process (which is
    # every test module) do not share counters — see api/deps/ratelimit.py.
    app.state.login_rate_limiter = LoginRateLimiter(
        attempts=settings.login_rate_limit_attempts,
        window_seconds=settings.login_rate_limit_window_seconds,
    )

    _configure_cors(app, settings)

    _register_exception_handlers(app)

    # Business endpoints. Every one of these takes its DB connection from
    # api/deps/db.py — see that module's header before adding another.
    from api.routers import (
        auth, evaluation, exams, health, jobs, papers, questions, students, upload,
    )

    # The two routes that answer without a bearer token, and the only two:
    # POST /auth/login (which IS the credential-granting endpoint) and
    # GET /health. Everything mounted after this point requires one —
    # tests/test_api/test_auth.py::test_no_endpoint_is_reachable_without_a_token
    # derives that list from the app's own OpenAPI schema and fails if it
    # grows.
    app.include_router(health.router)
    app.include_router(auth.router)

    app.include_router(upload.router)
    app.include_router(jobs.router)
    app.include_router(evaluation.router)
    # Question bank + paper generation. questions.py carries the mandatory
    # two-gate review flow (draft -> confirmed -> live); papers.py can only
    # ever draw from questions that finished it.
    app.include_router(questions.router)
    app.include_router(papers.router)
    app.include_router(exams.router)
    app.include_router(students.router)

    # No conditional router registration below this line. See the module
    # docstring: the app's route table is the same in every environment, so
    # what /docs renders in development is what production serves.

    return app


def _configure_cors(app: FastAPI, settings: Settings) -> None:
    """Installs CORSMiddleware, refusing the one combination that quietly
    disables the whole policy.

    `allow_credentials=True` together with `allow_origins=["*"]` does NOT do
    what it reads as. Starlette cannot send `Access-Control-Allow-Origin: *`
    on a credentialed response (browsers reject that pairing), so it echoes
    the request's own Origin back instead, with
    `Access-Control-Allow-Credentials: true`. The result is not "no origin
    restriction, no credentials" — it is *every* origin allowed to make
    credentialed requests. Verified against this app: a preflight from
    https://evil.example came back allowing https://evil.example.

    That is harmless today only because identity is a header a caller must set
    deliberately (api/deps/identity.py), so no browser attaches it
    automatically. The moment auth becomes a cookie or a session — which is
    the direction this build is heading — that config is a cross-site request
    forgery hole open to the entire internet, introduced by a setting nobody
    changed.

    So: credentials are enabled only when the origins are actually named. With
    a wildcard the API still serves cross-origin reads, but without
    credentials, which is the honest meaning of "*".
    """
    origins = settings.cors_origin_list
    wildcard = "*" in origins

    if wildcard and len(origins) > 1:
        raise ValueError(
            f"CORS_ORIGINS mixes '*' with specific origins ({origins}). "
            f"Starlette treats any '*' as allow-all, so the named origins are "
            f"decoration and the effective policy is wider than it reads. "
            f"Set either '*' or an explicit list."
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=not wildcard,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(TenantContextError)
    def _tenant_context_error(request: Request, exc: TenantContextError) -> JSONResponse:
        """A missing/ineffective RLS context is a 500, not a 404 or an empty
        list.

        This handler is the whole point of TenantContextError having its own
        type: the failure it represents (CLAUDE_CONTEXT.md §6 — RLS fails
        closed silently) would otherwise surface to the client as plausible-
        looking empty data. It is logged at ERROR and answered as a server
        misconfiguration, which is what it is.
        """
        log.error("tenant context failure on %s %s: %s", request.method, request.url.path, exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "tenant_context_error",
                "detail": str(exc),
            },
        )

    @app.exception_handler(Exception)
    def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        """Last-resort handler so an unexpected error is a logged 500 with a
        stable JSON shape rather than a bare ASGI traceback.

        The exception's message goes to the LOG always, and to the CLIENT only
        when debug endpoints are enabled. An unhandled exception here is most
        often psycopg2's, and psycopg2 puts the failing statement in its
        message — table names, column names, and any literal that was
        interpolated into it. Returning that verbatim hands an unauthenticated
        caller a partial schema dump from any endpoint they can crash. The
        operator who needs the text has the log; the client gets a stable
        shape and nothing else.
        """
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        settings = getattr(app.state, "settings", None)
        expose = bool(settings and settings.debug_endpoints_enabled)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "internal_error",
                "detail": str(exc) if expose else (
                    "An internal error occurred. The details are in the server "
                    "log; they are withheld here because an exception message "
                    "can carry SQL and schema details."
                ),
            },
        )


app = create_app()
