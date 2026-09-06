"""api/logging_config.py — JSON logs, and the request id that ties one
request's log lines (and, for a booklet job, its worker's log lines) together.

════════════════════════════════════════════════════════════════════════════
WHAT WAS MISSING, AND WHY IT MATTERS HERE SPECIFICALLY
════════════════════════════════════════════════════════════════════════════

Before this pass there was no structured logging and no request id
(CLAUDE_CONTEXT.md §11, "Known gaps"). `logging.getLogger("api")` and
`"api.auth"` already existed and already logged real things (the catch-all
exception handler, rejected tokens, tenant-context failures) — but as plain
text, one process's stdout among many workers', with nothing connecting one
request's log line to the response it produced, or to the WORKER log lines a
queued job it created writes minutes later in a different process. An
operator debugging "this college's evaluate call failed" had a timestamp and
a hope.

════════════════════════════════════════════════════════════════════════════
THE MECHANISM: ONE CONTEXTVAR, ONE FILTER, TWO SETTERS
════════════════════════════════════════════════════════════════════════════

`_request_id_ctx` is a `contextvars.ContextVar`. Exactly two things ever set
it:

  * api/main.py's request-id middleware, for the lifetime of one HTTP
    request — reads the inbound `X-Request-ID` header if the caller sent one
    (so a request can be traced across a gateway that generates its own),
    else mints a fresh one.
  * scripts/run_job_worker.py, for the lifetime of processing one claimed
    job — reads `job["payload"]["request_id"]`, the value the API wrote into
    the payload when it enqueued the job (api/services/ingestion.py,
    api/services/evaluation.py). A chained booklet_eval job inherits its
    ingest job's id (enqueue_chained_evaluation), so both halves of one
    booklet's pipeline, across two workers on two different days, still share
    one id.

`_RequestIdFilter`, attached once to the root logger's handler by
`configure_logging()`, reads the contextvar on every log record and stamps
it — so every existing `log.info(...)` / `log.error(...)` call anywhere in
`api/` or `core/` picks this up for free, with no call site changes. A log
line emitted outside either bound scope (a script's own startup message, a
test) gets `request_id: "-"` rather than crashing on a missing attribute.

════════════════════════════════════════════════════════════════════════════
WHY A HAND-WRITTEN FORMATTER, NOT A THIRD-PARTY ONE
════════════════════════════════════════════════════════════════════════════

stdlib `logging` already does everything a JSON formatter needs to key off:
level, logger name, message, exception info. A `Formatter` subclass turning
those into one `json.dumps(...)` per line is a dozen lines and pins no new
dependency — `python-json-logger` or `structlog` would each be reasonable,
but this repo's own convention (§10: unpinned deps are already a stated
regret) leans against adding one for something this small.
"""
from __future__ import annotations

import contextlib
import contextvars
import datetime as dt
import json
import logging
import threading

_request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

#: Attributes every stdlib LogRecord already carries, computed once from a
#: throwaway record — used by JsonFormatter to find the EXTRA fields a caller
#: passed via `logging.info(..., extra={...})` and fold them into the JSON
#: line instead of dropping them, which is what str(record.msg) would do.
_STANDARD_RECORD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()
) | {"message", "asctime"}


def get_request_id() -> str | None:
    """The request id bound to the CURRENT context, or None if nothing bound
    one — a plain script run, or a test that never went through the
    middleware or the worker's per-job binding."""
    return _request_id_ctx.get()


@contextlib.contextmanager
def bind_request_id(request_id: str | None):
    """Binds `request_id` to every log line emitted inside this `with` block
    (and inside anything it calls, including across an `await` — contextvars
    propagate through asyncio tasks and through anyio's thread-offloading,
    which is how a sync FastAPI endpoint running in a worker thread still
    sees the value bound by the async middleware around it).

    Resets to whatever was bound before on exit, so nested binding (there is
    none today, but a future call site nesting one job inside another must
    not) restores the outer scope rather than clearing it.
    """
    token = _request_id_ctx.set(request_id)
    try:
        yield
    finally:
        _request_id_ctx.reset(token)


class _RequestIdFilter(logging.Filter):
    """Stamps `record.request_id` from the contextvar, unless the log call
    already supplied one explicitly via `extra={"request_id": ...}` — a
    future call site that names a request id it already knows (a different
    one than the ambient context) is allowed to override, not overwritten."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = get_request_id() or "-"
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: timestamp, level, logger, message,
    request_id, plus any `extra=` fields a call site passed, plus exception
    text when the record carries one.

    Deliberately does NOT special-case which fields are "safe" — the
    catch-all exception handler in api/main.py already decides, before it
    ever calls `log.exception(...)`, that the full text belongs in the log
    and NOT in the client response (CLAUDE_CONTEXT.md §11's hardening pass 1
    — psycopg2 puts table/column names in its messages). This formatter's job
    is only to make what already goes to the log machine-parseable.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": dt.datetime.fromtimestamp(
                record.created, dt.timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None) or "-",
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS and key != "request_id":
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_configure_lock = threading.Lock()
_configured = False


def configure_logging(level: int = logging.INFO) -> None:
    """Attaches one JSON-formatted, request-id-stamped handler to the root
    logger. Idempotent and additive.

    IDEMPOTENT because `api.main.create_app()` calls this on every app build,
    and the test suite builds many apps in one process (one per
    `make_client(...)` call) — without the guard, each build would add
    another handler and every log line would print once per app ever
    created.

    ADDITIVE — appends to `root.handlers` rather than replacing it — because
    replacing it would delete any handler pytest's own logging capture
    attaches to the root logger for the DURATION OF A TEST. A destructive
    `root.handlers = [...]` here would silently break `caplog` the moment a
    test used it, for a reason with no connection to what that test is
    actually asserting.
    """
    global _configured
    with _configure_lock:
        if _configured:
            return
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        handler.addFilter(_RequestIdFilter())
        root = logging.getLogger()
        root.addHandler(handler)
        if root.level == logging.NOTSET or root.level > level:
            root.setLevel(level)
        _configured = True
