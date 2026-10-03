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


def test_health_reports_the_worker_as_unknown_when_not_configured() -> None:
    client = TestClient(create_app(Settings(_env_file=None, device="cpu", worker_health_url="")))
    assert client.get("/api/v1/health").json()["worker"]["status"] == "unknown"


def test_health_reports_the_worker_up_and_down() -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = b'{"status": "ok"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/health"
    settings = Settings(_env_file=None, device="cpu", worker_health_url=url)
    try:
        assert (
            TestClient(create_app(settings)).get("/api/v1/health").json()["worker"]["status"]
            == "up"
        )
    finally:
        server.shutdown()
        server.server_close()
    body = TestClient(create_app(settings)).get("/api/v1/health").json()  # the port is closed now
    assert body["status"] == "ok"  # the API itself is fine
    assert body["worker"]["status"] == "down"
