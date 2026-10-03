"""FastAPI application factory. Run with ``uvicorn tarn_api.app:create_app --factory``."""

from fastapi import FastAPI
from pydantic import BaseModel

from tarn_adapters.compute import detect_device
from tarn_adapters.config import Settings, get_settings
from tarn_adapters.logging_setup import configure_logging
from tarn_api import __version__, errors
from tarn_api.backends import Backends, PostgresBackends
from tarn_api.routes import accounts, auth, registration, roster


class DeviceOut(BaseModel):
    kind: str
    name: str
    detail: str


class HealthOut(BaseModel):
    status: str
    version: str
    environment: str
    device: DeviceOut


def create_app(settings: Settings | None = None, backends: Backends | None = None) -> FastAPI:
    """``backends`` defaults to PostgreSQL (``tarn_app`` + ``tarn_auth``); engines connect on
    first use, so building the app (or its OpenAPI document) needs no database."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=settings.env == "production")
    app = FastAPI(
        title="Tarn AI Evaluation API",
        version=__version__,
        description="AI suggests; the teacher decides. Every college sees only its own data.",
    )
    app.state.backends = backends or PostgresBackends(settings)
    errors.install(app)

    @app.get("/api/v1/health", response_model=HealthOut, tags=["system"])
    def health() -> HealthOut:
        device = detect_device(settings.device)
        return HealthOut(
            status="ok",
            version=__version__,
            environment=settings.env,
            device=DeviceOut(kind=device.kind, name=device.name, detail=device.detail),
        )

    for module in (auth, registration, accounts, roster):
        app.include_router(module.router)
    return app
