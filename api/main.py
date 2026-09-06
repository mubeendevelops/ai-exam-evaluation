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
import uuid

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.deps.db import TenantContextError
from api.deps.quota import SlidingWindowLimiter
from api.deps.ratelimit import LoginRateLimiter
from api.logging_config import bind_request_id, configure_logging, get_request_id
from api.settings import Settings, get_settings, load_dotenv_once

log = logging.getLogger("api")

#: Echoed back verbatim if the caller sends one (so a request can be traced
#: across a gateway that mints its own), generated fresh otherwise. See
#: api/logging_config.py for how this ties one request's log lines together.
REQUEST_ID_HEADER = "X-Request-ID"


def create_app(settings: Settings | None = None) -> FastAPI:
    load_dotenv_once()
    settings = settings or get_settings()
    configure_logging()

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

    # Per-college rate limits for the endpoints that spend real Groq tokens
    # or real CPU/DB writes on every call — api/deps/quota.py::rate_limit(name)
    # looks these up by name. One dict per app instance, same reasoning as
    # login_rate_limiter above: two apps in one process (every test module)
    # must not share counters.
    app.state.rate_limiters = {
        "evaluate": SlidingWindowLimiter(
            limit=settings.evaluate_rate_limit_attempts,
            window_seconds=settings.evaluate_rate_limit_window_seconds,
        ),
        "questions_generate": SlidingWindowLimiter(
            limit=settings.questions_generate_rate_limit_attempts,
            window_seconds=settings.questions_generate_rate_limit_window_seconds,
        ),
        "papers_generate": SlidingWindowLimiter(
            limit=settings.papers_generate_rate_limit_attempts,
            window_seconds=settings.papers_generate_rate_limit_window_seconds,
        ),
        "upload": SlidingWindowLimiter(
            limit=settings.upload_rate_limit_attempts,
            window_seconds=settings.upload_rate_limit_window_seconds,
        ),
    }

    _configure_cors(app, settings)

    _register_request_id_middleware(app)

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


def _register_request_id_middleware(app: FastAPI) -> None:
    """Binds one request id to every log line this request produces, and
    echoes it back as `X-Request-ID` on every response — including error
    responses, which is the point: see the hardening-pass note on the
    exception handlers below for why the id belongs in an error BODY too.

    Added AFTER `_configure_cors` (`app.add_middleware` wraps outside-in, so
    the LAST one added is the OUTERMOST), so this runs first on the way in
    and last on the way out — every response CORS produces, including a
    preflight's, still gets the header, and `request.state.request_id` is
    already set by the time an exception handler runs (Starlette's exception
    handling lives inside this middleware in the stack, not outside it).

    `bind_request_id` uses a contextvar, which anyio's thread-offloading
    propagates into the worker thread a sync endpoint actually runs in — so
    `log.info(...)` calls made from deep inside a router or a core/ function
    during this request pick up the id with no call-site changes.
    """

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id
        with bind_request_id(request_id):
            response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


def _request_id_for(request: Request) -> str:
    """The id to put in an error body — request.state if the middleware set
    it, the contextvar as a fallback (belt and braces; nothing today reaches
    an exception handler without going through the middleware first), and a
    literal "unknown" only if neither did, which would itself be a bug in the
    middleware wiring rather than a normal outcome."""
    return getattr(request.state, "request_id", None) or get_request_id() or "unknown"


def _register_exception_handlers(app: FastAPI) -> None:
    # WHY BOTH HANDLERS SET `extra={"request_id": ...}` EXPLICITLY, AND SET
    # THE RESPONSE HEADER THEMSELVES, RATHER THAN LEANING ON THE MIDDLEWARE.
    #
    # A handler registered for the bare `Exception` class is not run inside
    # ExceptionMiddleware like every other handler here — Starlette pulls it
    # out and hands it to ServerErrorMiddleware instead, which is
    # UNCONDITIONALLY THE OUTERMOST layer of the stack, outside every
    # `add_middleware` call regardless of order. So `_unhandled` runs OUTSIDE
    # `_register_request_id_middleware`'s `with bind_request_id(...):` block
    # — by the time it executes, that block's `finally` has already reset the
    # contextvar (the exception unwound through it on its way out), and
    # Starlette additionally dispatches a SYNC handler like this one through
    # `run_in_threadpool`, i.e. a DIFFERENT OS THREAD, where a contextvar
    # binding from the request's thread would not apply even if it were
    # still set. Concretely: `get_request_id()` here returns None and the
    # response never reaches the middleware's own `response.headers[...] =`
    # line at all, because ServerErrorMiddleware sends the response directly
    # rather than returning it back up through the stack.
    #
    # `request.state.request_id` has neither problem — it was set as a plain
    # attribute on the Request object before any of this, and Request is the
    # one thing both layers still share. So it is read directly here instead
    # of relying on the contextvar, for the log line (`extra=`, which the
    # filter in api/logging_config.py only fills in when a call site has NOT
    # already supplied one) and for the response, on BOTH count.
    def _finish(response: JSONResponse, request: Request) -> JSONResponse:
        response.headers[REQUEST_ID_HEADER] = _request_id_for(request)
        return response

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
        request_id = _request_id_for(request)
        log.error("tenant context failure on %s %s: %s", request.method, request.url.path, exc,
                  extra={"request_id": request_id})
        return _finish(JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "tenant_context_error",
                "detail": str(exc),
                # The safe correlation handle: this text is never SQL, but an
                # operator still needs to find THIS request's full log line
                # among every other request's, and the id is what lets them.
                "request_id": request_id,
            },
        ), request)

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

        `exc_info=exc` rather than `log.exception(...)` (which reads
        `sys.exc_info()`): Starlette runs this handler via `run_in_threadpool`
        — a DIFFERENT thread than the one the exception was raised in — and
        `sys.exc_info()` is thread-local, so it would come back empty there.
        Passing the exception object directly sidesteps that entirely.
        """
        request_id = _request_id_for(request)
        log.error("unhandled error on %s %s", request.method, request.url.path,
                  exc_info=exc, extra={"request_id": request_id})
        settings = getattr(app.state, "settings", None)
        expose = bool(settings and settings.debug_endpoints_enabled)
        return _finish(JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "internal_error",
                "detail": str(exc) if expose else (
                    "An internal error occurred. The details are in the server "
                    "log; they are withheld here because an exception message "
                    "can carry SQL and schema details."
                ),
                # The full exception text stays server-side (logged above,
                # with the request id explicitly attached — see this
                # function's docstring on why that cannot rely on the
                # ambient contextvar here). This is the client's correlation
                # handle to it — safe to return because it is never derived
                # from the exception, only from the request.
                "request_id": request_id,
            },
        ), request)


app = create_app()
