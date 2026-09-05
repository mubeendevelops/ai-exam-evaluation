"""api/routers/jobs.py — GET /api/v1/jobs/{job_id}: poll a queued evaluation.

THE CROSS-TENANT RULE THIS FILE EXISTS TO ENFORCE: a job belonging to another
college returns 404 — the same 404 as a job id that does not exist anywhere.
Not 403, and not the job.

403 would be wrong, not merely unfriendly. "403 Forbidden" says *this exists,
you may not have it*, which turns the endpoint into an existence oracle: a
caller can enumerate uuids and learn which ones name real jobs in other
colleges, and how many jobs another college is running. 404 for both cases
leaks nothing, at the cost of a slightly less helpful error for a caller who
genuinely mistyped their own job id — an acceptable trade, and the standard
one for tenant-scoped resources.

TWO INDEPENDENT MECHANISMS keep that true, and both are load-bearing:

  1. RLS. get_tenant_conn() sets app.current_college_id for the transaction,
     and migration 014's tenant_isolation policy filters every row.
  2. An explicit `AND college_id = %s` predicate inside
     core/jobs.py::get_job().

(2) is not redundant with (1) today — it is currently the ONLY one that works.
Postgres exempts SUPERUSER and BYPASSRLS roles from row-level security, and
`FORCE ROW LEVEL SECURITY` does not change that; with PGUSER=postgres (the
.env.example default) every policy in migrations 003 and 014 is inert. See
api/README.md's "Known gap". When a non-superuser application role exists,
(1) starts working and (2) becomes the belt to its braces. Do not delete
either.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status

import core.jobs
from api.deps.db import get_tenant_conn
from api.deps.identity import CurrentUser, get_current_user
from api.schemas.jobs import JobResponse, job_to_response

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    summary="Get one evaluation job's status, progress and error",
    responses={404: {"description": "No such job for this college."}},
)
def get_job(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_tenant_conn),
) -> JobResponse:
    """Returns the job if it belongs to the caller's college, else 404.

    `job_id` is typed as uuid.UUID so a malformed id is rejected by FastAPI as
    a 422 before any SQL runs — a non-uuid string reaching the query would
    raise inside Postgres's cast and surface as a 500.
    """
    with conn.cursor() as cur:
        job = core.jobs.get_job(cur, job_id=job_id, college_id=user.college_id)

    if job is None:
        # Identical response for "does not exist" and "belongs to another
        # college" — see the module docstring. Do not add the distinction
        # back in, however helpful it looks in a debugging session.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No job {job_id} for this college.",
        )

    return job_to_response(job)

