"""tests/test_api/test_exams.py — GET /api/v1/exams.

`exams` is one of migration 003's original seven RLS-protected tables, so
this is a straightforward cross-tenant isolation proof, the same shape as
test_jobs.py's — plus the status filter and MAX_LIMIT clamp every list
endpoint in this pass shares.
"""
from __future__ import annotations

import pytest

from tests.test_api.conftest import COLLEGE_A, COLLEGE_B

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

EXAMS = "/api/v1/exams"

#: seed_minimal.sql's exams — one per college.
COLLEGE_A_EXAM = "dddddddd-0001-0001-0001-dddddddddddd"
COLLEGE_B_EXAM = "dddddddd-0002-0002-0002-dddddddddddd"


async def test_exams_list_is_isolated_by_tenant(make_client, college_b):
    async with make_client(COLLEGE_A) as client:
        mine = await client.get(EXAMS)
    async with make_client(COLLEGE_B) as client:
        theirs = await client.get(EXAMS)

    assert mine.status_code == theirs.status_code == 200
    mine_ids = {e["exam_id"] for e in mine.json()["items"]}
    theirs_ids = {e["exam_id"] for e in theirs.json()["items"]}

    assert COLLEGE_A_EXAM in mine_ids
    assert COLLEGE_A_EXAM not in theirs_ids
    assert COLLEGE_B_EXAM in theirs_ids
    assert COLLEGE_B_EXAM not in mine_ids


async def test_exams_list_filters_by_status(make_client):
    async with make_client(COLLEGE_A) as client:
        live = await client.get(EXAMS, params={"status": "live"})
        closed = await client.get(EXAMS, params={"status": "closed"})

    assert live.status_code == closed.status_code == 200
    assert COLLEGE_A_EXAM in {e["exam_id"] for e in live.json()["items"]}
    assert COLLEGE_A_EXAM not in {e["exam_id"] for e in closed.json()["items"]}


async def test_exams_list_rejects_an_unknown_status(make_client):
    async with make_client(COLLEGE_A) as client:
        response = await client.get(EXAMS, params={"status": "archived"})

    assert response.status_code == 422, response.text


async def test_exams_list_limit_above_max_is_clamped_not_rejected(make_client):
    async with make_client(COLLEGE_A) as client:
        response = await client.get(EXAMS, params={"limit": 100_000})

    assert response.status_code == 200, response.text
    assert response.json()["limit"] == 200


async def test_exams_list_requires_credentials(make_client):
    """An empty Authorization header is no credential at all.

    Sent as an OVERRIDE of the client's own header rather than by building a
    bare client: httpx merges a client's default headers into every request,
    so a per-request dict that omits the key does not remove it — it is
    merged straight back in. Overriding it with an empty value is what
    actually clears it.
    """
    async with make_client(COLLEGE_A) as client:
        response = await client.get(EXAMS, headers={"Authorization": ""})

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"
