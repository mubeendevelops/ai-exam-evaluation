"""P21 over HTTP: rate limits on the public endpoints, request-size caps that hold without a
Content-Length, security headers, and a suspended college's tokens. In-memory backends."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from tarn_adapters.config import Settings
from tarn_api.app import create_app
from tarn_api.middleware import DEFAULT_BODY_LIMIT
from tarn_api.ratelimit import FORGOT_ACCOUNT, LOGIN_ACCOUNT, LOGIN_IP, TOO_MANY, client_address
from tarn_api.testing import MemoryBackends
from tarn_core.testing.auth_world import ADMIN_PASSWORD, AuthWorld

ADMIN = "admin@a.example"
LOGIN = "/api/v1/auth/login"


def _login(client: TestClient, email: str = ADMIN, password: str = ADMIN_PASSWORD) -> int:
    body = {"institution_id": "COLLEGE_A", "email": email, "password": password}
    return client.post(LOGIN, json=body).status_code


@pytest.fixture
def registered(world: AuthWorld) -> AuthWorld:
    world.register("COLLEGE_A", ADMIN)
    return world


# --- rate limits ------------------------------------------------------------------------------


def test_one_account_is_limited_whether_or_not_it_exists(
    client: TestClient, registered: AuthWorld
) -> None:
    for email in (ADMIN, "nobody@a.example"):
        codes = [_login(client, email, "wrong password!") for _ in range(LOGIN_ACCOUNT.count)]
        assert set(codes) == {401}
        refused = client.post(
            LOGIN,
            json={"institution_id": "COLLEGE_A", "email": email, "password": "x" * 12},
        )
        assert refused.status_code == 429
        assert refused.json() == {"detail": TOO_MANY}
        assert 0 < int(refused.headers["Retry-After"]) <= 15 * 60


def test_the_account_limit_lifts_after_its_window(
    client: TestClient, backends: MemoryBackends, registered: AuthWorld
) -> None:
    for _ in range(LOGIN_ACCOUNT.count + 1):
        _login(client, "nobody@a.example", "wrong password!")
    assert _login(client, "nobody@a.example", "wrong password!") == 429
    backends.mem.clock.advance(seconds=LOGIN_ACCOUNT.window.total_seconds() + 1)
    assert _login(client) == 200


def test_many_accounts_from_one_address_are_limited(client: TestClient) -> None:
    codes = [
        _login(client, f"guess{i}@a.example", "wrong password!") for i in range(LOGIN_IP.count + 1)
    ]
    assert codes[:-1] == [401] * LOGIN_IP.count and codes[-1] == 429


def test_forgot_password_cannot_flood_one_inbox(client: TestClient, registered: AuthWorld) -> None:
    body = {"institution_id": "COLLEGE_A", "email": ADMIN}
    codes = [
        client.post("/api/v1/auth/password/forgot", json=body).status_code
        for _ in range(FORGOT_ACCOUNT.count + 1)
    ]
    assert codes == [202] * FORGOT_ACCOUNT.count + [429]
    resets = [m for m in registered.mem.mailer.sent if "Reset your" in m.subject]
    assert len(resets) == FORGOT_ACCOUNT.count


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/v1/registrations/availability?institution_id=X_ONE", None),
        ("post", "/api/v1/registrations/verify-email", {"token": "0" * 40}),
        ("post", "/api/v1/auth/password/reset", {"token": "0" * 40, "new_password": "x" * 14}),
        ("post", "/api/v1/auth/invitations/accept", {"token": "0" * 40, "password": "x" * 14}),
    ],
)
def test_token_and_lookup_endpoints_are_limited_per_address(
    client: TestClient, method: str, path: str, body: dict[str, str] | None
) -> None:
    codes = []
    for _ in range(31):
        response = client.request(method.upper(), path, json=body)
        codes.append(response.status_code)
    assert 429 in codes and codes.index(429) >= 20


def test_registration_is_limited_per_address(client: TestClient) -> None:
    codes = []
    for i in range(6):
        form = {
            "institution_id": f"NEW_{i}",
            "admin_name": "Admin",
            "email": f"a{i}@new.example",
            "password": "an uncommon long passphrase",
        }
        codes.append(client.post("/api/v1/registrations", json=form).status_code)
    assert codes == [202] * 5 + [429]


def _request(peer: str, forwarded: list[str]) -> Request:
    headers = [(b"x-forwarded-for", value.encode()) for value in forwarded]
    return Request({"type": "http", "client": (peer, 1), "headers": headers})


def test_the_client_address_trusts_only_the_proxies_it_is_told_about() -> None:
    lb = ["203.0.113.9, 198.51.100.7, 35.191.0.1"]  # spoofed, client, load balancer
    assert client_address(_request("10.0.0.5", lb), 0) == "10.0.0.5"
    assert client_address(_request("10.0.0.5", lb), 2) == "198.51.100.7"
    assert client_address(_request("10.0.0.5", ["198.51.100.7"]), 2) == "10.0.0.5"


def test_limits_can_be_switched_off_for_load_tests(backends: MemoryBackends) -> None:
    settings = Settings(_env_file=None, device="cpu", rate_limits_enabled=False)
    with TestClient(create_app(settings, backends=backends)) as client:
        assert {_login(client, "x@a.example", "wrong password!") for _ in range(70)} == {401}


# --- request size caps -------------------------------------------------------------------------


def _chunks(size: int, chunk: int = 64 * 1024) -> Iterator[bytes]:
    sent = 0
    while sent < size:
        yield b"x" * min(chunk, size - sent)
        sent += chunk


def test_a_declared_oversized_body_is_refused_before_it_is_read(client: TestClient) -> None:
    response = client.post(
        LOGIN,
        content=b"{}",
        headers={"content-length": str(DEFAULT_BODY_LIMIT + 1), "content-type": "application/json"},
    )
    assert response.status_code == 413


def test_a_chunked_body_is_cut_off_at_the_cap(client: TestClient, registered: AuthWorld) -> None:
    body = {"institution_id": "COLLEGE_A", "email": ADMIN, "password": ADMIN_PASSWORD}
    token = client.post(LOGIN, json=body).json()["access_token"]
    response = client.post(
        "/api/v1/roster/import",
        content=_chunks(DEFAULT_BODY_LIMIT + 1024),
        headers={"Authorization": f"Bearer {token}", "content-type": "text/csv"},
    )
    assert response.status_code == 413
    assert "larger than" in response.json()["detail"]


def test_booklet_uploads_get_their_own_cap(backends: MemoryBackends) -> None:
    settings = Settings(_env_file=None, device="cpu", upload_max_bytes=1024)
    with TestClient(create_app(settings, backends=backends)) as client:
        response = client.post(
            "/api/v1/booklets",
            content=_chunks(3 * 1024 * 1024),
            headers={"content-type": "multipart/form-data; boundary=x"},
        )
    assert response.status_code == 413


# --- headers -----------------------------------------------------------------------------------


def test_every_api_response_carries_the_security_headers(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "strict-transport-security" not in response.headers  # development: plain http
    bloom = client.get("/api/v1/auth/password-bloom")
    assert bloom.headers["cache-control"].startswith("public")  # a route's own choice stays
    assert "content-security-policy" not in client.get("/docs").headers


def test_production_adds_hsts() -> None:
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    from tarn_api.middleware import SecurityHeaders

    inner = Starlette(routes=[Route("/", lambda _request: PlainTextResponse("ok"))])
    with TestClient(SecurityHeaders(inner, hsts=True)) as client:
        assert client.get("/").headers["strict-transport-security"] == "max-age=31536000"


# --- suspension over HTTP -------------------------------------------------------------------


def test_a_suspended_colleges_access_token_stops_at_the_next_request(
    client: TestClient, registered: AuthWorld
) -> None:
    body = {"institution_id": "COLLEGE_A", "email": ADMIN, "password": ADMIN_PASSWORD}
    token = client.post(LOGIN, json=body).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
    college = registered.mem.identity.find_tenant("COLLEGE_A")
    assert college is not None
    registered.registration.suspend(college.college_id, operator="ops@tarn")
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert client.post("/api/v1/auth/refresh").status_code == 401
    assert _login(client) == 401
