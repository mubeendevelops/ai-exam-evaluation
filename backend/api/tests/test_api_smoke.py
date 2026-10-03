"""Smoke test for tarn_api."""

from fastapi.testclient import TestClient

from tarn_adapters.config import Settings
from tarn_api.app import create_app


def test_health_reports_ok_and_device() -> None:
    client = TestClient(create_app(Settings(_env_file=None, device="cpu")))
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["device"]["kind"] == "cpu"
