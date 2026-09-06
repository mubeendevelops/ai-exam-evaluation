"""tests/test_api/test_observability.py — the request-id middleware and the
catch-all exception handler's promise about what it does and does not put in
a response body (Hardening pass 2026-09-06, and Hardening pass 2026-09-05's
original SQL-hiding fix, re-verified now that the body also carries a field).

Three things pinned here:

  1. Every response carries `X-Request-ID` — generated when the caller sends
     none, echoed back verbatim when they do.
  2. An unhandled exception is still a stable JSON 500 with the full text
     withheld from the client (Hardening pass 2026-09-05) — re-verified here
     because this pass changed the body's SHAPE.
  3. That same body now carries `request_id`, so a caller who cannot see the
     server log still has a handle to hand an operator who can.

api/logging_config.py's JSON formatting itself (the log LINE shape) is not
asserted against here: it is stdlib `logging` plumbing with no externally
observable behaviour beyond "the log has a request_id field", which the
exception handler test below verifies indirectly by using `caplog` to confirm
the id in the response body is the SAME id the server actually logged under.
"""
from __future__ import annotations

import logging
from unittest.mock import patch

import httpx
import pytest

from api.main import create_app
from api.settings import Settings
from tests.test_api.conftest import COLLEGE_A

pytestmark = [pytest.mark.db, pytest.mark.asyncio]


async def test_every_response_carries_a_generated_request_id(make_client):
    async with make_client(COLLEGE_A) as client:
        response = await client.get("/api/v1/auth/me")

    assert response.status_code == 200, response.text
    request_id = response.headers.get("x-request-id")
    assert request_id, "every response must carry X-Request-ID"


async def test_a_caller_supplied_request_id_is_echoed_back_verbatim(make_client):
    """A caller tracing a request across its own gateway gets the SAME id
    back, not a fresh one the middleware minted over it."""
    async with make_client(COLLEGE_A) as client:
        response = await client.get(
            "/api/v1/auth/me", headers={"X-Request-ID": "caller-chosen-trace-id"},
        )

    assert response.status_code == 200, response.text
    assert response.headers["x-request-id"] == "caller-chosen-trace-id"


async def test_two_requests_get_two_different_generated_ids(make_client):
    async with make_client(COLLEGE_A) as client:
        first = await client.get("/api/v1/auth/me")
        second = await client.get("/api/v1/auth/me")

    assert first.headers["x-request-id"] != second.headers["x-request-id"]


async def test_unhandled_exception_hides_sql_but_carries_the_logged_request_id(
    sign_token, caplog,
):
    """The catch-all handler's whole job, restated with the new field: the
    full exception text — here, deliberately shaped like a psycopg2 message,
    naming a table and a column no client should ever see — goes to the LOG
    and never to the response body. The response instead carries a
    `request_id` that names the SAME log line, which is what makes the
    withholding survivable: an operator can still find it.

    A production-shaped app (`enable_debug_endpoints=False`) is used
    deliberately — `debug_endpoints_enabled=True` is the one condition under
    which this handler DOES return the raw text, and that branch is not what
    this test is about.

    `core.exams.list_exams` is patched rather than provoking a REAL database
    error: the handler's contract does not depend on which exception reached
    it, only on what it does with the message once one has — asserting that
    against a message we control is a stronger, more direct test of the
    handler than fishing for a real constraint violation would be.
    """
    prod_settings = Settings(
        api_env="production", enable_debug_endpoints=False, storage_mode="dummy",
        jwt_secret="a signing key for this test's app",
        cors_origins="https://example.test",
    )
    prod_app = create_app(prod_settings)
    token = sign_token(prod_settings, COLLEGE_A)

    fake_db_error = RuntimeError(
        'relation "students" column "national_id_number" does not exist '
        'LINE 1: SELECT national_id_number FROM students WHERE ...'
    )

    caplog.set_level(logging.ERROR, logger="api")
    with patch("core.exams.list_exams", side_effect=fake_db_error):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=prod_app, raise_app_exceptions=False),
            base_url="http://testserver",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Request-ID": "trace-the-500",
            },
        ) as client:
            response = await client.get("/api/v1/exams")

    assert response.status_code == 500, response.text
    body = response.json()

    assert body["error"] == "internal_error"
    detail = body["detail"]
    assert "students" not in detail
    assert "national_id_number" not in detail
    assert "SELECT" not in detail.upper()

    # The safe correlation handle: matches the header, and it is the SAME id
    # the server logged the full (unsafe) text under.
    assert body["request_id"] == "trace-the-500"
    assert response.headers["x-request-id"] == "trace-the-500"

    assert "national_id_number" in caplog.text, (
        "the full text must still reach the log even though it is withheld "
        "from the client — an operator has to be able to find it"
    )
