"""An SME's profile fields over HTTP (P36, D225): the admin sets the employee ID and department
when adding an SME and edits them and the name in the directory (``PUT /accounts/{id}``); the SME
reads them in ``/auth/me`` and cannot change them; validation; audit; another college's SME."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tarn_adapters.config import Settings
from tarn_api.app import create_app
from tarn_api.security import ROLE_REFUSED
from tarn_api.testing import MemoryBackends
from tarn_core.domain.audit import AuditAction
from tarn_core.seed import DEV_PASSWORD
from tarn_core.testing.seed_world import seed_in_memory

BASE = "/api/v1"
ANITA = "anita.desai@demo-com.example.test"


class Demo:
    def __init__(self, client: TestClient, backends: MemoryBackends) -> None:
        self.client = client
        self.backends = backends
        self.admin = self.login("DEMO_COM", "admin@demo-com.example.test")
        self.suresh = self.login("DEMO_COM", "suresh.naik@demo-com.example.test")
        self.anita = self.login("DEMO_COM", ANITA)
        self.b_admin = self.login("DEMO_ENG", "admin@demo-eng.example.test")
        found = self.get(f"{BASE}/auth/me", self.anita)["user"]
        self.anita_id, self.anita_name = found["id"], found["display_name"]

    def login(self, institution: str, email: str) -> dict[str, str]:
        response = self.client.post(
            f"{BASE}/auth/login",
            json={"institution_id": institution, "email": email, "password": DEV_PASSWORD},
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def get(self, path: str, who: dict[str, str]) -> Any:
        response = self.client.get(path, headers=who)
        assert response.status_code == 200, response.text
        return response.json()

    def edit(self, who: dict[str, str], user_id: str, **body: Any) -> Any:
        return self.client.put(f"{BASE}/accounts/{user_id}", headers=who, json=body)


@pytest.fixture
def demo() -> Iterator[Demo]:
    backends = MemoryBackends()
    seed_in_memory(backends.mem)
    app = create_app(
        Settings(_env_file=None, device="cpu", rate_limits_enabled=False), backends=backends
    )
    with TestClient(app) as client:
        yield Demo(client, backends)


def _fields(
    name: str = "Anita Desai", employee_id: str | None = None, dept: str | None = None
) -> dict[str, str | None]:
    return {"display_name": name, "employee_id": employee_id, "department": dept}


def test_the_seeded_sme_has_no_fields_yet(demo: Demo) -> None:
    user = demo.get(f"{BASE}/auth/me", demo.anita)["user"]
    assert (user["employee_id"], user["department"]) == (None, None)


def test_the_admin_edits_and_the_sme_reads_their_own_profile(demo: Demo) -> None:
    done = demo.edit(
        demo.admin, demo.anita_id, **_fields("Dr. Anita Desai", "TKS-8904", "Commerce")
    )
    assert done.status_code == 200, done.text
    assert done.json()["employee_id"] == "TKS-8904"
    me = demo.get(f"{BASE}/auth/me", demo.anita)["user"]
    assert (me["display_name"], me["employee_id"], me["department"]) == (
        "Dr. Anita Desai",
        "TKS-8904",
        "Commerce",
    )
    listed = {a["user"]["id"]: a["user"] for a in demo.get(f"{BASE}/accounts", demo.admin)}
    assert listed[demo.anita_id]["department"] == "Commerce"
    # The email, and so the way to sign in, did not change.
    assert me["email"] == ANITA
    demo.login("DEMO_COM", ANITA)


def test_values_are_trimmed_and_blank_clears(demo: Demo) -> None:
    demo.edit(demo.admin, demo.anita_id, **_fields(employee_id="  E-1  ", dept="Physics"))
    cleared = demo.edit(demo.admin, demo.anita_id, **_fields(employee_id="E-1", dept="   "))
    assert cleared.status_code == 200
    assert (cleared.json()["employee_id"], cleared.json()["department"]) == ("E-1", None)


def test_validation(demo: Demo) -> None:
    ok = demo.edit(demo.admin, demo.anita_id, **_fields(employee_id="x" * 40, dept="d" * 80))
    assert ok.status_code == 200, ok.text
    for body in (
        _fields(employee_id="x" * 41),
        _fields(dept="d" * 81),
        _fields(name=""),
        _fields(name="n" * 201),
        {"display_name": "Anita", "employee_id": None},  # every field is sent
        {**_fields(), "email": "someone.else@demo-com.example.test"},  # not editable here
    ):
        response = demo.edit(demo.admin, demo.anita_id, **body)
        assert response.status_code == 422, (body, response.text)
    # A name of spaces is blank once trimmed: refused by the core, as 422 too.
    assert demo.edit(demo.admin, demo.anita_id, **_fields(name="   ")).status_code == 422
    after = demo.get(f"{BASE}/auth/me", demo.anita)["user"]
    assert (after["employee_id"], after["department"]) == ("x" * 40, "d" * 80)


def test_an_sme_cannot_edit_a_profile(demo: Demo) -> None:
    for who in (demo.anita, demo.suresh):
        for target in (demo.anita_id,):
            response = demo.edit(who, target, **_fields(employee_id="MINE-1"))
            assert response.status_code == 403
            assert response.json() == {"detail": ROLE_REFUSED}
    assert demo.get(f"{BASE}/auth/me", demo.anita)["user"]["employee_id"] is None
    unauthenticated = demo.client.put(f"{BASE}/accounts/{demo.anita_id}", json=_fields())
    assert unauthenticated.status_code == 401


def test_adding_an_sme_takes_the_fields(demo: Demo) -> None:
    subjects = demo.get(f"{BASE}/subjects", demo.admin)
    made = demo.client.post(
        f"{BASE}/accounts",
        headers=demo.admin,
        json={
            "display_name": "Prof. New Person",
            "email": "new.person@demo-com.example.test",
            "subject_ids": [subjects[0]["id"]],
            "employee_id": " TKS-77 ",
            "department": "  ",
        },
    )
    assert made.status_code == 201, made.text
    user = made.json()["user"]
    assert (user["employee_id"], user["department"]) == ("TKS-77", None)
    too_long = demo.client.post(
        f"{BASE}/accounts",
        headers=demo.admin,
        json={
            "display_name": "Prof. Long",
            "email": "long@demo-com.example.test",
            "employee_id": "x" * 41,
        },
    )
    assert too_long.status_code == 422


def test_the_edit_is_audited_with_field_names_and_values_and_no_more(demo: Demo) -> None:
    demo.edit(demo.admin, demo.anita_id, **_fields("Anita D.", "TKS-1", None))
    demo.edit(demo.admin, demo.anita_id, **_fields("Anita D.", "TKS-1", None))  # nothing changed
    events = [e for e in demo.backends.mem.audit.events if e.action is AuditAction.ACCOUNT_UPDATED]
    assert len(events) == 1
    assert events[0].before == {
        "user_id": demo.anita_id,
        "display_name": demo.anita_name,
        "employee_id": None,
    }
    assert events[0].after == {
        "user_id": demo.anita_id,
        "display_name": "Anita D.",
        "employee_id": "TKS-1",
    }


def test_another_colleges_admin_finds_nobody(demo: Demo) -> None:
    response = demo.edit(demo.b_admin, demo.anita_id, **_fields("Taken", "B-1", "B"))
    assert response.status_code == 404
    user = demo.get(f"{BASE}/auth/me", demo.anita)["user"]
    assert (user["display_name"], user["employee_id"]) == (demo.anita_name, None)
