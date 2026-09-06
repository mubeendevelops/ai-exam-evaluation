"""tests/test_api/test_students.py — GET /api/v1/students.

`students` is one of migration 003's original seven RLS-protected tables —
cross-tenant isolation, the exam filter (which goes through `answers`, since
there is no direct student->exam FK — see core/students.py), and the shared
MAX_LIMIT clamp.
"""
from __future__ import annotations

import pytest

from tests.test_api.conftest import COLLEGE_A, COLLEGE_B

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

STUDENTS = "/api/v1/students"

#: seed_minimal.sql's students — one for college A, one for college B.
COLLEGE_A_STUDENT = "cccccccc-0001-0001-0001-cccccccccccc"
COLLEGE_B_STUDENT = "cccccccc-0003-0003-0003-cccccccccccc"


async def test_students_list_is_isolated_by_tenant(make_client, college_b):
    async with make_client(COLLEGE_A) as client:
        mine = await client.get(STUDENTS)
    async with make_client(COLLEGE_B) as client:
        theirs = await client.get(STUDENTS)

    assert mine.status_code == theirs.status_code == 200
    mine_ids = {s["student_id"] for s in mine.json()["items"]}
    theirs_ids = {s["student_id"] for s in theirs.json()["items"]}

    assert COLLEGE_A_STUDENT in mine_ids
    assert COLLEGE_A_STUDENT not in theirs_ids
    assert COLLEGE_B_STUDENT in theirs_ids
    assert COLLEGE_B_STUDENT not in mine_ids


async def test_students_list_filters_by_exam(make_client, make_booklet):
    """Only students with an answer on the named exam appear — the join
    core/students.py::list_students makes onto `answers`, since there is no
    direct student->exam link in this schema."""
    booklet = make_booklet()

    async with make_client(COLLEGE_A) as client:
        for_exam = await client.get(STUDENTS, params={"exam_id": booklet["exam_id"]})
        for_ghost_exam = await client.get(
            STUDENTS, params={"exam_id": "00000000-0000-0000-0000-000000000000"}
        )

    assert for_exam.status_code == 200, for_exam.text
    assert booklet["student_id"] in {s["student_id"] for s in for_exam.json()["items"]}
    assert for_ghost_exam.json()["items"] == []
    assert for_ghost_exam.json()["total"] == 0


async def test_students_list_limit_above_max_is_clamped_not_rejected(make_client):
    async with make_client(COLLEGE_A) as client:
        response = await client.get(STUDENTS, params={"limit": 100_000})

    assert response.status_code == 200, response.text
    assert response.json()["limit"] == 200


async def test_students_list_requires_credentials(make_client):
    async with make_client(COLLEGE_A) as client:
        response = await client.get(STUDENTS, headers={"Authorization": ""})

    assert response.status_code == 401, response.text
