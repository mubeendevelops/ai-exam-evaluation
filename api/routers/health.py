"""api/routers/health.py — the liveness probe.

The ONE endpoint besides POST /auth/login that answers without a credential,
and the only one that answers without touching the database.

WHY IT DOES NOT CHECK THE DATABASE

A health check that runs a query is a readiness probe, and it is also an
unauthenticated way for anyone on the network to make this process open a
connection and run SQL — a cheap amplifier, and one that gets worse under
exactly the load a health check is polled hardest. What a load balancer needs
from this path is "is the process alive and serving", which is what returning
at all proves.

A readiness endpoint that verifies the database (and MinIO, and the worker's
heartbeat) is a real and separate thing; when it is added it takes an
admin credential, because it reports on infrastructure.

WHAT IT DOES NOT REPORT: versions, hostnames, settings, environment names.
An unauthenticated endpoint is read by everyone, and "which version are you
running" is the first question an attacker asks.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe — no credential, no database",
)
def health() -> HealthResponse:
    """`{"status": "ok"}`, always, as long as the process is serving."""
    return HealthResponse(status="ok")
