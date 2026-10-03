"""The worker's health endpoint: ``GET /health`` answers while the process is alive.

The API calls it to show the worker in the status pill of the web app. It carries no student
data and no secrets."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Handler(BaseHTTPRequestHandler):
    device: str = "cpu"

    def do_GET(self) -> None:
        if self.path.rstrip("/") != "/health":
            self.send_error(404)
            return
        body = json.dumps({"status": "ok", "device": self.device}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Health probes every few seconds would flood the log."""


def start_health_server(host: str, port: int, device: str) -> ThreadingHTTPServer:
    """Serves in a daemon thread; the caller calls ``shutdown()`` and ``server_close()``."""
    handler = type("Handler", (_Handler,), {"device": device})
    server = ThreadingHTTPServer((host, port), handler)
    threading.Thread(target=server.serve_forever, name="worker-health", daemon=True).start()
    return server
