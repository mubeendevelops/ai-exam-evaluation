"""FastAPI application factory. Run with ``uvicorn tarn_api.app:create_app --factory``."""

import json
import urllib.error
import urllib.request
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from tarn_adapters.compute import detect_device
from tarn_adapters.config import Settings, get_settings
from tarn_adapters.logging_setup import configure_logging
from tarn_api import __version__, errors
from tarn_api.backends import Backends, PostgresBackends
from tarn_api.middleware import BodyLimit, SecurityHeaders, body_rules
from tarn_api.ratelimit import RateLimiter
from tarn_api.routes import (
    accounts,
    auth,
    blueprints,
    booklets,
    diagrams,
    evaluated,
    questions,
    registration,
    review,
    roster,
    segments,
)
from tarn_core.services.question_bank import DIAGRAM_MAX_BYTES, KEY_FILE_MAX_BYTES


class DeviceOut(BaseModel):
    kind: str
    name: str
    detail: str


class WorkerOut(BaseModel):
    status: Literal["up", "down", "unknown"]
    detail: str


class HealthOut(BaseModel):
    status: str
    version: str
    environment: str
    device: DeviceOut
    worker: WorkerOut


def probe_worker(url: str, timeout_s: float = 1.0) -> WorkerOut:
    """Asks the worker's health endpoint; "unknown" when no address is configured."""
    if not url:
        return WorkerOut(status="unknown", detail="Worker address not configured")
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:  # noqa: S310 - own config
            body = json.load(response)
    except (urllib.error.URLError, OSError, ValueError):
        return WorkerOut(status="down", detail="Worker not reachable")
    if isinstance(body, dict) and body.get("status") == "ok":
        return WorkerOut(status="up", detail="Worker running")
    return WorkerOut(status="down", detail="Worker reports a problem")


def create_app(
    settings: Settings | None = None,
    backends: Backends | None = None,
    *,
    rate_limiter: RateLimiter | None = None,
) -> FastAPI:
    """``backends`` defaults to PostgreSQL (``tarn_app`` + ``tarn_auth``); engines connect on
    first use, so building the app (or its OpenAPI document) needs no database. The rate
    limiter counts on the backends' clock unless one is given."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=settings.env == "production")
    app = FastAPI(
        title="Tarn AI Evaluation API",
        version=__version__,
        description="AI suggests; the teacher decides. Every college sees only its own data.",
    )
    app.state.backends = chosen = backends or PostgresBackends(settings)
    app.state.rate_limiter = rate_limiter or RateLimiter(
        chosen.clock.now, enabled=settings.rate_limits_enabled
    )
    app.state.trusted_proxy_hops = settings.trusted_proxy_hops
    errors.install(app)
    # The last one added runs first: the body cap sees the request before anything reads it.
    app.add_middleware(SecurityHeaders, hsts=settings.env == "production")
    app.add_middleware(
        BodyLimit,
        rules=body_rules(settings.upload_max_bytes, KEY_FILE_MAX_BYTES, DIAGRAM_MAX_BYTES),
    )

    @app.get("/api/v1/health", response_model=HealthOut, tags=["system"])
    def health() -> HealthOut:
        device = detect_device(settings.device)
        return HealthOut(
            status="ok",
            version=__version__,
            environment=settings.env,
            device=DeviceOut(kind=device.kind, name=device.name, detail=device.detail),
            worker=probe_worker(settings.worker_health_url),
        )

    for module in (
        auth,
        registration,
        accounts,
        roster,
        blueprints,
        questions,
        booklets,
        diagrams,
        evaluated,
        review,
        segments,
    ):
        app.include_router(module.router)
    return app
