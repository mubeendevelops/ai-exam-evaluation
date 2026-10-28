"""Units of work over HTTP (P26): the admin creates and lists units, offered only entitled
SMEs; the SME lists their own; the unit detail shows progress and never marks (D167)."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tarn_adapters.config import Settings
from tarn_api.app import create_app
from tarn_api.testing import MemoryBackends, queued_booklet, upload_for_paper
from tarn_core.seed import DEV_PASSWORD
from tarn_core.testing import fake_pdf
from tarn_core.testing.seed_world import seed_in_memory

BASE = "/api/v1"
ADMIN = "admin@demo-com.example.test"
SURESH = "suresh.naik@demo-com.example.test"
ANITA = "anita.desai@demo-com.example.test"


class Demo:
    def __init__(self, client: TestClient, backends: MemoryBackends) -> None:
        self.client = client
        self.backends = backends
        self.admin = self.login(ADMIN)
        self.suresh = self.login(SURESH)
        self.anita = self.login(ANITA)

    def login(self, email: str) -> dict[str, str]:
        response = self.client.post(
            f"{BASE}/auth/login",
            json={"institution_id": "DEMO_COM", "email": email, "password": DEV_PASSWORD},
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def units(self, headers: dict[str, str], **params: str) -> list[dict[str, Any]]:
        response = self.client.get(f"{BASE}/units", headers=headers, params=params)
        assert response.status_code == 200, response.text
        body: list[dict[str, Any]] = response.json()
        return body


@pytest.fixture
def demo() -> Iterator[Demo]:
    backends = MemoryBackends()
    seed_in_memory(backends.mem)
    app = create_app(
        Settings(_env_file=None, device="cpu", rate_limits_enabled=False), backends=backends
    )
    with TestClient(app) as client:
        yield Demo(client, backends)


def test_the_seed_units_are_listed_for_the_admin_and_each_sme(demo: Demo) -> None:
    every = demo.units(demo.admin)
    assert len(every) == 4 and {u["code"] for u in every} == {f"U-000{n}" for n in range(1, 5)}
    mine = demo.units(demo.suresh)
    assert {u["owner"]["name"] for u in mine} == {"Suresh Naik (demo SME)"} and len(mine) == 2
    # An SME's owner filter is ignored: they see their own units only.
    anita_id = demo.units(demo.anita)[0]["owner"]["id"]
    assert demo.units(demo.suresh, owner_id=anita_id) == mine
    assert len(demo.units(demo.admin, owner_id=anita_id)) == 2
    subject = every[0]["subject"]["id"]
    assert {u["subject"]["id"] for u in demo.units(demo.admin, subject_id=subject)} == {subject}
    assert demo.units(demo.admin, state="open") == every


def test_the_admin_creates_a_unit_with_an_entitled_sme(demo: Demo) -> None:
    c = demo.client
    papers = c.get(f"{BASE}/units/papers", headers=demo.admin).json()
    paper = next(p for p in papers if p["exam_code"] == "QP-CI-2021-06")
    smes = c.get(
        f"{BASE}/units/smes", params={"subject_id": paper["subject"]["id"]}, headers=demo.admin
    ).json()
    assert {s["email"] for s in smes} == {SURESH, ANITA}
    body = {
        "paper_id": paper["id"],
        "year": "2025",
        "owner_id": smes[0]["id"],
        "due_date": "2030-01-31",
    }
    made = c.post(f"{BASE}/units", json=body, headers=demo.admin)
    assert made.status_code == 201, made.text
    unit = made.json()
    assert unit["code"] == "U-0005" and unit["year"] == "2025" and unit["state"] == "open"
    assert unit["paper"]["exam_code"] == "QP-CI-2021-06" and unit["counts"]["total"] == 0
    sent = demo.backends.kit.mailer.sent[-1]  # type: ignore[attr-defined]
    assert "U-0005" in sent.text and "2030-01-31" in sent.text
    assert c.post(f"{BASE}/units", json=body, headers=demo.admin).status_code == 409
    assert c.post(f"{BASE}/units", json=body, headers=demo.suresh).status_code == 403
    admin_id = c.get(f"{BASE}/auth/me", headers=demo.admin).json()["user"]["id"]
    wrong = c.post(
        f"{BASE}/units", json={**body, "year": "2026", "owner_id": admin_id}, headers=demo.admin
    )
    assert wrong.status_code == 422 and "active SME" in wrong.json()["detail"]
    past = c.post(
        f"{BASE}/units", json={**body, "year": "2027", "due_date": "2020-01-01"}, headers=demo.admin
    )
    assert past.status_code == 422


def test_the_unit_detail_shows_progress_and_no_marks(demo: Demo) -> None:
    c = demo.client
    unit = next(u for u in demo.units(demo.admin) if u["paper"]["exam_code"] == "QP-CI-2021-06")
    student = c.get(f"{BASE}/students", headers=demo.admin).json()[0]
    booklet = queued_booklet(
        upload_for_paper(c, demo.admin, unit["paper"]["id"], student["usn"], fake_pdf(1, "u"))
    )
    detail = c.get(f"{BASE}/units/{unit['id']}", headers=demo.admin).json()
    assert detail["counts"]["total"] == 1 and detail["counts"]["processing"] == 1
    (row,) = detail["booklets"]
    assert row["id"] == booklet and row["result"] is None and row["unit"]["code"] == unit["code"]
    assert detail["max_waiting"] == 5 and detail["waiting"] == 1
    assert c.get(f"{BASE}/units/{unit['id']}", headers=demo.suresh).status_code == 200
    assert c.get(f"{BASE}/units/{unit['id']}", headers=demo.anita).status_code == 404
    listed = c.get(f"{BASE}/booklets", params={"unit_id": unit["id"]}, headers=demo.suresh)
    assert [b["id"] for b in listed.json()["items"]] == [booklet]
    assert c.get(f"{BASE}/booklets/{booklet}", headers=demo.anita).status_code == 404
    for path in ("review", "result-sheets", "segments"):
        assert c.get(f"{BASE}/booklets/{booklet}/{path}", headers=demo.admin).status_code == 403
    assert c.get(f"{BASE}/evaluated-booklets", headers=demo.admin).status_code == 403
