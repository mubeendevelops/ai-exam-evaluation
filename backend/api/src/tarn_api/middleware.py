"""Request-size caps and response security headers (P21), as plain ASGI middleware.

**Body caps.** Starlette spools a multipart upload whole before a route sees it, and
``request.body()`` reads any length, so a route's own size check comes too late (and a chunked
request has no Content-Length to check). ``BodyLimit`` counts the bytes as they arrive and stops
at the cap with 413: booklet uploads may carry ``upload_max_bytes`` (plus room for the form),
key files and reference diagrams their own limit, everything else (JSON, the roster CSV) 2 MiB.

**Headers.** Every API response is ``nosniff``, never framed, sends no referrer, and is not
cached unless the route says otherwise (responses hold student data). Production adds HSTS.
The interactive docs (``/docs``) load Swagger UI from its CDN, so they get no CSP."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MIB = 1024 * 1024
DEFAULT_BODY_LIMIT = 2 * MIB


@dataclass(frozen=True, slots=True)
class BodyRule:
    method: str
    path: re.Pattern[str]
    limit: int


def body_rules(upload_max_bytes: int, key_file_bytes: int, diagram_bytes: int) -> list[BodyRule]:
    return [
        BodyRule("POST", re.compile(r"^/api/v1/booklets$"), upload_max_bytes + MIB),
        BodyRule("POST", re.compile(r"^/api/v1/questions/[^/]+/key-files$"), key_file_bytes + MIB),
        BodyRule("POST", re.compile(r"^/api/v1/questions/[^/]+/diagrams$"), diagram_bytes + MIB),
    ]


def _too_large(limit: int) -> str:
    return f"The request is larger than {max(1, limit // MIB)} MB."


class BodyLimit:
    def __init__(
        self, app: ASGIApp, *, rules: Sequence[BodyRule], default: int = DEFAULT_BODY_LIMIT
    ) -> None:
        self.app = app
        self.rules = tuple(rules)
        self.default = default

    def limit_for(self, method: str, path: str) -> int:
        for rule in self.rules:
            if rule.method == method and rule.path.match(path):
                return rule.limit
        return self.default

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.limit_for(scope["method"], scope["path"])
        for name, value in scope["headers"]:
            if name == b"content-length" and (not value.isdigit() or int(value) > limit):
                response = JSONResponse(
                    {"detail": _too_large(limit)}, status.HTTP_413_CONTENT_TOO_LARGE
                )
                await response(scope, receive, send)
                return
        seen = 0

        async def counted() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    # An HTTPException: FastAPI passes it through its body parsing as it is.
                    raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail=_too_large(limit))
            return message

        await self.app(scope, counted, send)


_DOCS = ("/docs", "/redoc", "/openapi.json")


class SecurityHeaders:
    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        docs = scope["path"].startswith(_DOCS)

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                wanted: list[tuple[bytes, bytes]] = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cache-control", b"no-store"),
                    (b"cross-origin-resource-policy", b"same-origin"),
                ]
                if not docs:
                    wanted.append(
                        (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'")
                    )
                if self.hsts:
                    wanted.append((b"strict-transport-security", b"max-age=31536000"))
                headers += [(k, v) for k, v in wanted if k not in present]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, with_headers)
