"""The API on PostgreSQL: tarn_app on a throwaway application database, tarn_auth on a
throwaway identity database (``make up`` first; ``make test-integration``)."""

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tarn_adapters.auth.mail import ConsoleMailer
from tarn_adapters.config import Settings
from tarn_adapters.identity.testing import create_test_identity_database
from tarn_adapters.postgres.testing import create_test_database, drop_test_database
from tarn_api.app import create_app
from tarn_api.backends import PostgresBackends
from tarn_core.ports.identity import EmailMessage

pytestmark = pytest.mark.integration

PASSWORD = "a long integration passphrase"


class CapturingMailer(ConsoleMailer):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def send(self, message: EmailMessage) -> None:
        self.links += [w for w in message.text.split() if "token=" in w]


@pytest.fixture(scope="module")
def setup(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[TestClient, CapturingMailer]]:
    base = Settings()
    app_db = create_test_database(base.database_url, base.app_database_url)
    identity_db = create_test_identity_database(base.database_url, base.identity_app_database_url)
    keys: Path = tmp_path_factory.mktemp("keys")
    settings = Settings(
        app_database_url=app_db.app_url,
        identity_app_database_url=identity_db.app_url,
        local_key_dir=keys,
        tenant_signup_requires_approval=False,
        device="cpu",
    )
    backends = PostgresBackends(settings)
    mailer = CapturingMailer()
    backends.kit = replace(backends.kit, mailer=mailer)
    try:
        with TestClient(create_app(settings, backends=backends)) as client:
            yield client, mailer
    finally:
        backends.dispose()
        drop_test_database(base.database_url, app_db.name)
        drop_test_database(base.database_url, identity_db.name)


def _register(client: TestClient, mailer: CapturingMailer, institution: str, email: str) -> str:
    form = {"institution_id": institution, "admin_name": "Admin", "email": email}
    r = client.post("/api/v1/registrations", json=form | {"password": PASSWORD})
    assert r.status_code == 202, r.text
    token = mailer.links[-1].split("token=")[1]
    assert (
        client.post("/api/v1/registrations/verify-email", json={"token": token}).json()["status"]
        == "ACTIVE"
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"institution_id": institution, "email": email, "password": PASSWORD},
    )
    assert login.status_code == 200, login.text
    return str(login.json()["access_token"])


def test_two_colleges_over_http_on_postgres(setup: tuple[TestClient, CapturingMailer]) -> None:
    client, mailer = setup
    a = {"Authorization": f"Bearer {_register(client, mailer, 'PG_A', 'admin@pg-a.example')}"}
    b = {"Authorization": f"Bearer {_register(client, mailer, 'PG_B', 'admin@pg-b.example')}"}
    csv = "name,usn,class/section\nSynthetic Student,1PG21CS001,A\n"
    r = client.post("/api/v1/roster/import", content=csv, headers=a | {"Content-Type": "text/csv"})
    assert r.json()["created"] == 1
    assert [s["usn"] for s in client.get("/api/v1/students", headers=a).json()] == ["1PG21CS001"]
    assert client.get("/api/v1/students", headers=b).json() == []
    invited = client.post(
        "/api/v1/accounts", json={"display_name": "T", "email": "t@pg-a.example"}, headers=a
    )
    assert invited.status_code == 201
    teacher_id = invited.json()["user"]["id"]
    assert client.post(f"/api/v1/accounts/{teacher_id}/disable", headers=b).status_code == 404
    emails_b = [x["user"]["email"] for x in client.get("/api/v1/accounts", headers=b).json()]
    assert emails_b == ["admin@pg-b.example"]
    # Lockout persists across requests (counters committed although sign-in failed).
    wrong = {"institution_id": "PG_B", "email": "admin@pg-b.example", "password": "nope nope"}
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json=wrong).status_code == 401
    right = wrong | {"password": PASSWORD}
    assert client.post("/api/v1/auth/login", json=right).status_code == 401
