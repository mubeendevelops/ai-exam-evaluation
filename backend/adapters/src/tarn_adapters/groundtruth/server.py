"""The transcription page: a small local web server (standard library only) that shows a cleaned
page with the engines' reading pre-filled for every line, for a person to correct.

Student pages and text pass through it, so it is built to stay on this machine: it binds to
127.0.0.1, requires a random token on every request (the URL it prints carries it), answers only
to the Host names of that address (no DNS rebinding), serves the page with a strict
Content-Security-Policy, and never sends anything out. Lines are shown with ``textContent`` and
input values, never as HTML."""

import json
import re
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, urlsplit

from tarn_core.domain.groundtruth import TruthPage
from tarn_core.errors import InvariantError, NotFoundError
from tarn_core.ports.groundtruth import GroundTruthStore
from tarn_core.services.groundtruth import GroundTruthService, line_from_record

MAX_BODY = 2_000_000
_PAGE_PATH = re.compile(r"^/api/pages/([a-z0-9][a-z0-9._-]{0,63})(/image)?$")
CSP = (
    "default-src 'none'; img-src 'self'; script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'"
)


def _summary(page: TruthPage) -> dict[str, Any]:
    counts = {"prefilled": 0, "verified": 0, "ignored": 0}
    for line in page.lines:
        counts[line.status.value] += 1
    return {"id": page.id, "capture": page.capture.value, "source": page.source, **counts}


def _detail(page: TruthPage) -> dict[str, Any]:
    return {
        **_summary(page),
        "width": page.width,
        "height": page.height,
        "lines": [
            {
                "box": [ln.box.x0, ln.box.y0, ln.box.x1, ln.box.y1],
                "text": ln.text,
                "class": ln.content_class.value,
                "status": ln.status.value,
                "origin": ln.origin.value,
                "prefill": ln.prefill,
                "region_id": None if ln.region_id is None else str(ln.region_id),
            }
            for ln in page.lines
        ],
    }


class TranscriptionServer:
    """``serve_forever`` blocks; ``start`` runs it on a thread (tests)."""

    def __init__(self, store: GroundTruthStore, *, host: str = "127.0.0.1", port: int = 0) -> None:
        self.token = secrets.token_urlsafe(24)
        self._store = store
        self._service = GroundTruthService(store)
        self._lock = threading.Lock()  # one save at a time: a page is read, changed and written
        outer = self

        class Handler(_Handler):
            server_app = outer

        self._httpd = ThreadingHTTPServer((host, port), Handler)
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    def serve_forever(self) -> None:
        self._httpd.serve_forever()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    # -- the API, independent of HTTP ---------------------------------------------------------

    def list_pages(self) -> list[dict[str, Any]]:
        return [_summary(p) for p in self._service.pages()]

    def page(self, page_id: str) -> dict[str, Any]:
        return _detail(self._store.get(page_id))

    def image(self, page_id: str) -> tuple[bytes, str]:
        page = self._store.get(page_id)
        kind = "image/png" if page.image.lower().endswith(".png") else "image/jpeg"
        return self._store.image(page_id), kind

    def save(self, page_id: str, body: object) -> dict[str, Any]:
        if not isinstance(body, dict) or not isinstance(body.get("lines"), list):
            raise InvariantError('the body is {"lines": [...]}')
        lines = []
        for number, item in enumerate(body["lines"], start=1):
            if not isinstance(item, dict):
                raise InvariantError(f"line {number}: not an object")
            lines.append(line_from_record(item, number))
        with self._lock:
            return _detail(self._service.save_lines(page_id, lines))


class _Handler(BaseHTTPRequestHandler):
    server_app: TranscriptionServer
    server_version = "tarn-transcribe"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return  # request lines hold page ids only, but the console is not the place for them

    # -- plumbing -----------------------------------------------------------------------------

    def _reply(
        self, status: HTTPStatus, body: bytes, content_type: str, *, page: bool = False
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if page:
            self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: HTTPStatus, value: object) -> None:
        self._reply(status, json.dumps(value).encode("utf-8"), "application/json")

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json(status, {"error": message})

    def _allowed(self, query: dict[str, list[str]]) -> bool:
        app = self.server_app
        host = (self.headers.get("Host") or "").lower()
        if host not in (f"127.0.0.1:{app.port}", f"localhost:{app.port}"):
            return False
        supplied = self.headers.get("X-Tarn-Token") or (query.get("t") or [""])[0]
        return secrets.compare_digest(supplied, app.token)

    def _route(self, method: str) -> None:
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        if not self._allowed(query):
            self._error(HTTPStatus.FORBIDDEN, "forbidden")
            return
        try:
            self._dispatch(method, parts.path)
        except NotFoundError:
            self._error(HTTPStatus.NOT_FOUND, "not found")
        except InvariantError as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))  # messages never hold line text

    def _dispatch(self, method: str, path: str) -> None:
        app = self.server_app
        if method == "GET" and path == "/":
            html = resources.files("tarn_adapters.groundtruth").joinpath("transcribe.html")
            self._reply(HTTPStatus.OK, html.read_bytes(), "text/html; charset=utf-8", page=True)
        elif method == "GET" and path == "/api/pages":
            self._json(HTTPStatus.OK, app.list_pages())
        else:
            match = _PAGE_PATH.match(path)
            if match is None:
                self._error(HTTPStatus.NOT_FOUND, "not found")
            elif method == "GET" and match.group(2):
                data, kind = app.image(match.group(1))
                self._reply(HTTPStatus.OK, data, kind)
            elif method == "GET":
                self._json(HTTPStatus.OK, app.page(match.group(1)))
            elif method == "PUT" and not match.group(2):
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too large")
                    return
                try:
                    body = json.loads(self.rfile.read(length))
                except ValueError:
                    raise InvariantError("the body is not JSON") from None
                self._json(HTTPStatus.OK, app.save(match.group(1), body))
            else:
                self._error(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")

    def do_GET(self) -> None:
        self._route("GET")

    def do_PUT(self) -> None:
        self._route("PUT")
