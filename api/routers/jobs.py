"""api/routers/jobs.py — GET /api/v1/jobs (list) and GET /api/v1/jobs/{job_id}
(poll one queued evaluation).

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

Both run since migration 016, which creates the NOSUPERUSER/NOBYPASSRLS
application role .env.example now points PGUSER at. Before it, (1) did nothing
at all — Postgres exempts SUPERUSER and BYPASSRLS roles from row-level
security, and `FORCE ROW LEVEL SECURITY` does not change that — so (2) was the
only isolation this endpoint had. That is still true for any deployment whose
PGUSER is a superuser, which is one .env edit away. Do not delete either.
"""
from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status

import core.jobs
from api.deps.db import get_tenant_conn
from api.deps.identity import CurrentUser, require_college_user
from api.deps.pagination import Pagination, get_pagination
from api.schemas.jobs import JobResponse, JobStatus, job_to_response
from api.schemas.pagination import Page

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@router.get(
    "",
    response_model=Page[JobResponse],
    summary="List and filter this college's jobs",
)
def list_jobs(
    status_filter: JobStatus | None = Query(
        default=None, alias="status",
        description="Filter by evaluation_job_status. `status` is aliased "
                    "from `status_filter` for the same reason "
                    "api/routers/questions.py does — `status` is also the "
                    "FastAPI status module imported in this file.",
    ),
    job_type: str | None = Query(
        default=None, description="e.g. 'booklet_ingest' or 'booklet_eval'.",
    ),
    created_after: dt.datetime | None = Query(
        default=None,
        description="Only jobs created strictly after this timestamp — "
                    "exclusive, so polling with the newest id you already "
                    "have cannot see it again.",
    ),
    user: CurrentUser = Depends(require_college_user),
    conn=Depends(get_tenant_conn),
    pagination: Pagination = Depends(get_pagination),
) -> Page[JobResponse]:
    """The job dashboard: this college's queue, newest first.

    Tenant-scoped exactly like GET /jobs/{job_id} — RLS plus the explicit
    college_id predicate in core/jobs.py::list_jobs, for the reasons that
    module's header and api/deps/db.py's argue at length.
    """
    with conn.cursor() as cur:
        jobs = core.jobs.list_jobs(
            cur, college_id=user.college_id, status=status_filter,
            job_type=job_type, created_after=created_after,
            limit=pagination.limit, offset=pagination.offset,
        )
        total = core.jobs.count_jobs(
            cur, college_id=user.college_id, status=status_filter,
            job_type=job_type, created_after=created_after,
        )

    return Page(
        items=[job_to_response(j) for j in jobs],
        total=total, limit=pagination.limit, offset=pagination.offset,
    )


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    summary="Get one evaluation job's status, progress and error",
    responses={404: {"description": "No such job for this college."}},
)
def get_job(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_college_user),
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

