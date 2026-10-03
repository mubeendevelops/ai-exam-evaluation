"""P4 over HTTP: sign-in, refresh cookie, lockout, reset links, recovery codes, registration,
roles, roster and cross-college denial. In-memory backends; synthetic data."""

import pytest
from fastapi.testclient import TestClient

from tarn_api.security import REFRESH_COOKIE
from tarn_api.testing import MemoryBackends
from tarn_core.domain.identity import TenantStatus
from tarn_core.ids import CollegeId, UserId
from tarn_core.testing.auth_world import ADMIN_PASSWORD, TEACHER_PASSWORD, AuthWorld

ADMIN = "admin@a.example"
TEACHER = "teacher@a.example"


def _login(
    client: TestClient, institution: str, email: str, password: str, *, remember: bool = False
) -> dict[str, object]:
    response = client.post(
        "/api/v1/auth/login",
        json={
            "institution_id": institution,
            "email": email,
            "password": password,
            "remember": remember,
        },
    )
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def _bearer(body: dict[str, object]) -> dict[str, str]:
    return {"Authorization": f"Bearer {body['access_token']}"}


@pytest.fixture
def college(world: AuthWorld) -> tuple[CollegeId, UserId, UserId]:
    cid, admin = world.register("COLLEGE_A", ADMIN)
    teacher = world.invite(cid, admin, TEACHER)
    return cid, admin, teacher


# --- sign-in ----------------------------------------------------------------------------------


def test_login_me_refresh_logout(
    client: TestClient, college: tuple[CollegeId, UserId, UserId]
) -> None:
    body = _login(client, "college_a", ADMIN, ADMIN_PASSWORD)  # ID is upper-cased
    assert body["status"] == "signed_in" and body["token_type"] == "bearer"
    cookie = client.cookies.get(REFRESH_COOKIE)
    assert cookie
    me = client.get("/api/v1/auth/me", headers=_bearer(body))
    assert me.status_code == 200
    assert me.json()["user"]["role"] == "admin" and me.json()["institution_id"] == "COLLEGE_A"
    refreshed = client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200
    assert client.cookies.get(REFRESH_COOKIE) not in (None, cookie)
    # Replaying the first refresh token revokes the session.
    client.cookies.set(REFRESH_COOKIE, cookie, path="/api/v1/auth")
    assert client.post("/api/v1/auth/refresh").status_code == 401
    assert client.get("/api/v1/auth/me", headers=_bearer(refreshed.json())).status_code == 401
    again = _login(client, "COLLEGE_A", ADMIN, ADMIN_PASSWORD)
    assert client.post("/api/v1/auth/logout", headers=_bearer(again)).status_code == 204
    assert client.get("/api/v1/auth/me", headers=_bearer(again)).status_code == 401


def test_refresh_cookie_flags_and_remember(
    client: TestClient, college: tuple[CollegeId, UserId, UserId]
) -> None:
    def cookie_header(remember: bool) -> str:
        response = client.post(
            "/api/v1/auth/login",
            json={"institution_id": "COLLEGE_A", "email": ADMIN, "password": ADMIN_PASSWORD,
                  "remember": remember},
        )  # fmt: skip
        return response.headers["set-cookie"].lower()

    short = cookie_header(False)
    assert "httponly" in short and "samesite=strict" in short and "path=/api/v1/auth" in short
    assert "max-age" not in short  # a browser-session cookie
    long = cookie_header(True)
    assert f"max-age={30 * 24 * 3600}" in long


def test_failures_look_the_same_and_lock_the_account(
    client: TestClient, backends: MemoryBackends, college: tuple[CollegeId, UserId, UserId]
) -> None:
    def attempt(institution: str, email: str, password: str) -> tuple[int, str]:
        r = client.post(
            "/api/v1/auth/login",
            json={"institution_id": institution, "email": email, "password": password},
        )
        return r.status_code, str(r.json().get("detail", ""))

    unknown_inst = attempt("NOPE", ADMIN, ADMIN_PASSWORD)
    unknown_user = attempt("COLLEGE_A", "x@a.example", ADMIN_PASSWORD)
    wrong = attempt("COLLEGE_A", TEACHER, "wrong password 1")
    assert unknown_inst == unknown_user == wrong and wrong[0] == 401
    for _ in range(4):
        attempt("COLLEGE_A", TEACHER, "wrong password 1")
    assert attempt("COLLEGE_A", TEACHER, TEACHER_PASSWORD)[0] == 401  # locked
    backends.mem.clock.advance(minutes=16)
    assert attempt("COLLEGE_A", TEACHER, TEACHER_PASSWORD)[0] == 200


def test_access_token_expires(
    client: TestClient, backends: MemoryBackends, college: tuple[CollegeId, UserId, UserId]
) -> None:
    body = _login(client, "COLLEGE_A", ADMIN, ADMIN_PASSWORD)
    backends.mem.clock.advance(minutes=15)
    assert client.get("/api/v1/auth/me", headers=_bearer(body)).status_code == 401
    assert (
        client.get("/api/v1/auth/me", headers={"Authorization": "Bearer junk"}).status_code == 401
    )
    assert client.get("/api/v1/auth/me").status_code == 401


# --- passwords --------------------------------------------------------------------------------


def test_forgot_reset_and_single_use(
    client: TestClient, backends: MemoryBackends, college: tuple[CollegeId, UserId, UserId]
) -> None:
    sent = len(backends.mem.mailer.sent)
    r = client.post("/api/v1/auth/password/forgot", json={"institution_id": "NOPE", "email": ADMIN})
    assert r.status_code == 202 and len(backends.mem.mailer.sent) == sent
    r = client.post(
        "/api/v1/auth/password/forgot", json={"institution_id": "COLLEGE_A", "email": TEACHER}
    )
    assert r.status_code == 202
    token = backends.mem.mailer.last_token(TEACHER)
    weak = client.post(
        "/api/v1/auth/password/reset", json={"token": token, "new_password": "password1234"}
    )
    assert weak.status_code == 422 and weak.json()["reasons"]
    good = {"token": token, "new_password": "teacher's new passphrase"}
    assert client.post("/api/v1/auth/password/reset", json=good).status_code == 204
    assert client.post("/api/v1/auth/password/reset", json=good).status_code == 400
    _login(client, "COLLEGE_A", TEACHER, "teacher's new passphrase")


def test_recovery_codes_over_http(
    client: TestClient, college: tuple[CollegeId, UserId, UserId]
) -> None:
    body = _login(client, "COLLEGE_A", TEACHER, TEACHER_PASSWORD)
    r = client.post(
        "/api/v1/auth/recovery-codes",
        json={"current_password": TEACHER_PASSWORD},
        headers=_bearer(body),
    )
    assert r.status_code == 201
    codes = r.json()["codes"]
    assert len(codes) == 10
    recover = {
        "institution_id": "COLLEGE_A",
        "email": TEACHER,
        "recovery_code": codes[0],
        "new_password": "recovered teacher passphrase",
    }
    assert client.post("/api/v1/auth/password/recover", json=recover).status_code == 204
    assert client.post("/api/v1/auth/password/recover", json=recover).status_code == 401
    me = client.get(
        "/api/v1/auth/me",
        headers=_bearer(_login(client, "COLLEGE_A", TEACHER, "recovered teacher passphrase")),
    )
    assert me.json()["recovery_codes_left"] == 9


def test_force_reset_answers_reset_required(
    client: TestClient, college: tuple[CollegeId, UserId, UserId]
) -> None:
    _, _, teacher = college
    admin = _login(client, "COLLEGE_A", ADMIN, ADMIN_PASSWORD)
    r = client.post(f"/api/v1/accounts/{teacher}/force-reset", headers=_bearer(admin))
    assert r.status_code == 204
    body = _login(client, "COLLEGE_A", TEACHER, TEACHER_PASSWORD)
    assert body["status"] == "reset_required" and "access_token" not in body


# --- registration -----------------------------------------------------------------------------


def test_registration_flow(client: TestClient, backends: MemoryBackends, world: AuthWorld) -> None:
    check = client.get("/api/v1/registrations/availability", params={"institution_id": "NEW_1"})
    assert check.json() == {"institution_id": "NEW_1", "valid": True, "available": True,
                            "problem": None}  # fmt: skip
    bad = client.get("/api/v1/registrations/availability", params={"institution_id": "new 1"})
    assert bad.json()["valid"] is False
    form = {
        "institution_id": "NEW_1",
        "admin_name": "Dr. New",
        "email": "head@new.example",
        "password": "a long new-college passphrase",
    }
    r = client.post("/api/v1/registrations", json=form)
    assert r.status_code == 202
    assert r.json() == {"institution_id": "NEW_1", "status": "PENDING_VERIFICATION",
                        "approval_required": True}  # fmt: skip
    assert client.post("/api/v1/registrations", json=form).status_code == 409
    taken = client.get("/api/v1/registrations/availability", params={"institution_id": "NEW_1"})
    assert taken.json()["available"] is False
    weak = client.post(
        "/api/v1/registrations", json=form | {"institution_id": "NEW_2", "password": "short"}
    )
    assert weak.status_code == 422
    invalid = client.post("/api/v1/registrations", json=form | {"institution_id": "NEW 3"})
    assert invalid.status_code == 422
    token = backends.mem.mailer.last_token("head@new.example")
    verified = client.post("/api/v1/registrations/verify-email", json={"token": token})
    assert verified.json()["status"] == "PENDING_APPROVAL"
    login = {"institution_id": "NEW_1", "email": "head@new.example", "password": form["password"]}
    assert client.post("/api/v1/auth/login", json=login).status_code == 401
    tenant = backends.mem.identity.find_tenant("NEW_1")
    assert tenant is not None
    assert world.registration.approve(tenant.college_id, operator="ops").status is (
        TenantStatus.ACTIVE
    )
    assert client.post("/api/v1/auth/login", json=login).status_code == 200


def test_registration_mail_is_not_sent_when_the_request_fails(
    client: TestClient, backends: MemoryBackends, world: AuthWorld
) -> None:
    world.register("TAKEN", "a@taken.example")
    sent = len(backends.mem.mailer.sent)
    form = {"institution_id": "TAKEN", "admin_name": "X", "email": "x@x.example",
            "password": "a long new-college passphrase"}  # fmt: skip
    assert client.post("/api/v1/registrations", json=form).status_code == 409
    assert len(backends.mem.mailer.sent) == sent


# --- roles ------------------------------------------------------------------------------------


def test_teachers_cannot_manage_accounts(
    client: TestClient, backends: MemoryBackends, college: tuple[CollegeId, UserId, UserId]
) -> None:
    _, admin_id, teacher_id = college
    teacher = _bearer(_login(client, "COLLEGE_A", TEACHER, TEACHER_PASSWORD))
    assert client.get("/api/v1/accounts", headers=teacher).status_code == 403
    invite = {"display_name": "X", "email": "x@a.example"}
    assert client.post("/api/v1/accounts", json=invite, headers=teacher).status_code == 403
    assert client.post(f"/api/v1/accounts/{admin_id}/disable", headers=teacher).status_code == 403
    admin = _bearer(_login(client, "COLLEGE_A", ADMIN, ADMIN_PASSWORD))
    assert client.post("/api/v1/accounts", json=invite, headers=admin).status_code == 201
    listed = client.get("/api/v1/accounts", headers=admin).json()
    assert {a["user"]["email"] for a in listed} == {ADMIN, TEACHER, "x@a.example"}
    # Disabling ends the teacher's access at once.
    assert client.post(f"/api/v1/accounts/{teacher_id}/disable", headers=admin).status_code == 200
    assert client.get("/api/v1/auth/me", headers=teacher).status_code == 401
    assert client.post(f"/api/v1/accounts/{admin_id}/disable", headers=admin).status_code == 403


def test_invitation_accept(client: TestClient, backends: MemoryBackends, world: AuthWorld) -> None:
    world.register("COLLEGE_A", ADMIN)
    admin = _bearer(_login(client, "COLLEGE_A", ADMIN, ADMIN_PASSWORD))
    client.post("/api/v1/accounts", json={"display_name": "N", "email": "n@a.example"},
                headers=admin)  # fmt: skip
    token = backends.mem.mailer.last_token("n@a.example")
    accept = {"token": token, "password": "new teacher passphrase"}
    assert client.post("/api/v1/auth/invitations/accept", json=accept).status_code == 204
    _login(client, "COLLEGE_A", "n@a.example", "new teacher passphrase")


# --- roster -----------------------------------------------------------------------------------

CSV = "name,usn,class/section\nAsha Rao,1AB21CS001,CSE-A\nRavi Kumar,1AB21CS002,CSE-B\n"


def test_roster_import_and_search(
    client: TestClient, college: tuple[CollegeId, UserId, UserId]
) -> None:
    teacher = _bearer(_login(client, "COLLEGE_A", TEACHER, TEACHER_PASSWORD))
    headers = teacher | {"Content-Type": "text/csv"}
    r = client.post("/api/v1/roster/import", content=CSV, headers=headers)
    assert r.status_code == 200 and r.json()["created"] == 2 and r.json()["imported"]
    bad = client.post("/api/v1/roster/import", content="name,usn\n,X\n", headers=headers)
    assert bad.json()["imported"] is False and bad.json()["errors"][0]["line"] == 2
    wrong_type = client.post(
        "/api/v1/roster/import", content=CSV, headers=teacher | {"Content-Type": "application/json"}
    )
    assert wrong_type.status_code == 415
    latin1 = client.post(
        "/api/v1/roster/import", content="name,usn\nJos\xe9,1AB21CS009\n".encode("latin-1"),
        headers=headers,
    )  # fmt: skip
    assert latin1.status_code == 422
    found = client.get("/api/v1/students", params={"q": "ravi"}, headers=teacher).json()
    assert [s["usn"] for s in found] == ["1AB21CS002"]


# --- across colleges --------------------------------------------------------------------------


def test_another_college_sees_nothing_of_this_one(
    client: TestClient, world: AuthWorld, college: tuple[CollegeId, UserId, UserId]
) -> None:
    _, _, teacher_a = college
    world.register("COLLEGE_B", "admin@b.example")
    a = _bearer(_login(client, "COLLEGE_A", TEACHER, TEACHER_PASSWORD))
    client.post("/api/v1/roster/import", content=CSV, headers=a | {"Content-Type": "text/csv"})
    b = _bearer(_login(client, "COLLEGE_B", "admin@b.example", ADMIN_PASSWORD))
    assert client.get("/api/v1/students", headers=b).json() == []
    assert client.post(f"/api/v1/accounts/{teacher_a}/disable", headers=b).status_code == 404
    assert client.post(f"/api/v1/accounts/{teacher_a}/unlock", headers=b).status_code == 404
    accounts_b = client.get("/api/v1/accounts", headers=b).json()
    assert [x["user"]["email"] for x in accounts_b] == ["admin@b.example"]
    # A's credentials do not open B.
    wrong = {"institution_id": "COLLEGE_B", "email": TEACHER, "password": TEACHER_PASSWORD}
    assert client.post("/api/v1/auth/login", json=wrong).status_code == 401


def test_password_bloom_is_served(client: TestClient) -> None:
    r = client.get("/api/v1/auth/password-bloom")
    assert r.status_code == 200 and r.content[:4] == b"TBF1" and len(r.content) > 100_000
