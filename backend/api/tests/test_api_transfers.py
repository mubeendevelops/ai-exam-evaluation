"""Transfers, subject removal, SME removal and the delegation inbox over HTTP (P27, D163)."""

from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from tarn_adapters.config import Settings
from tarn_adapters.stages import LocalStageRunner
from tarn_adapters.testing import scripted_stages
from tarn_api.app import create_app
from tarn_api.testing import MemoryBackends, queued_booklet
from tarn_core.ids import BookletId, CollegeId
from tarn_core.seed import DEV_PASSWORD
from tarn_core.testing import fake_pdf
from tarn_core.testing.seed_world import seed_in_memory

BASE = "/api/v1"
ADMIN = "admin@demo-com.example.test"
SURESH = "suresh.naik@demo-com.example.test"
ANITA = "anita.desai@demo-com.example.test"
REASON = "Suresh is on leave this month"

Headers = dict[str, str]


class Demo:
    def __init__(self, client: TestClient, backends: MemoryBackends) -> None:
        self.c = client
        self.backends = backends
        self.admin = self.login(ADMIN)
        self.suresh = self.login(SURESH)
        self.anita = self.login(ANITA)
        accounts = self.c.get(f"{BASE}/accounts", headers=self.admin).json()
        self.ids = {a["user"]["email"]: a["user"]["id"] for a in accounts}
        self.subjects = {a["user"]["email"]: set(a["subject_ids"]) for a in accounts}

    def login(self, email: str) -> Headers:
        response = self.c.post(
            f"{BASE}/auth/login",
            json={"institution_id": "DEMO_COM", "email": email, "password": DEV_PASSWORD},
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def units(self, headers: Headers, **params: str) -> list[dict[str, Any]]:
        response = self.c.get(f"{BASE}/units", headers=headers, params=params)
        assert response.status_code == 200, response.text
        found: list[dict[str, Any]] = response.json()
        return found

    def unit_of(self, email: str) -> dict[str, Any]:
        """The SME's unit, QP-CI first (the scripted pipeline knows that paper)."""
        mine = [u for u in self.units(self.admin) if u["owner"]["id"] == self.ids[email]]
        return sorted(mine, key=lambda u: u["paper"]["exam_code"] != "QP-CI-2021-06")[0]

    def transfer(self, unit: dict[str, Any], to: str, **body: object) -> Any:
        return self.c.post(
            f"{BASE}/units/{unit['id']}/transfer",
            json={"owner_id": self.ids[to], "reason": REASON} | body,
            headers=self.admin,
        )

    def scored_booklet(self, unit: dict[str, Any]) -> str:
        student = self.c.get(f"{BASE}/students", headers=self.admin).json()[0]
        booklet = queued_booklet(
            self.c.post(
                f"{BASE}/units/{unit['id']}/booklets",
                headers=self.admin,
                files=[("files", (f"{student['usn']}.pdf", fake_pdf(1, "t"), "application/pdf"))],
            )
        )
        runner = LocalStageRunner(scripted_stages(self.backends.mem.blobs), self.backends.mem)
        assert runner.run(CollegeId(UUID(int=0)), BookletId(UUID(int=0))) == []
        return booklet

    @property
    def mail(self) -> list[Any]:
        return self.backends.kit.mailer.sent  # type: ignore[attr-defined, no-any-return]


@pytest.fixture
def demo() -> Iterator[Demo]:
    backends = MemoryBackends()
    seed_in_memory(backends.mem)
    app = create_app(
        Settings(_env_file=None, device="cpu", rate_limits_enabled=False), backends=backends
    )
    with TestClient(app) as client:
        yield Demo(client, backends)


def _entitled_to(demo: Demo, email: str, subject: str) -> None:
    if subject not in demo.subjects[email]:
        r = demo.c.post(f"{BASE}/accounts/{demo.ids[email]}/subjects/{subject}", headers=demo.admin)
        assert r.status_code == 200, r.text


def test_a_transfer_moves_the_unit_and_the_old_lock_stops_working(demo: Demo) -> None:
    c = demo.c
    unit = demo.unit_of(SURESH)
    _entitled_to(demo, ANITA, unit["subject"]["id"])
    booklet = demo.scored_booklet(unit)
    review = c.post(f"{BASE}/booklets/{booklet}/lock", headers=demo.suresh).json()
    answer = next(a for a in review["answers"] if a["suggestion"])

    # Refusals first: nothing moves.
    assert demo.transfer(unit, SURESH).status_code == 422  # the owner already
    admin_id = c.get(f"{BASE}/auth/me", headers=demo.admin).json()["user"]["id"]
    wrong = c.post(
        f"{BASE}/units/{unit['id']}/transfer",
        json={"owner_id": admin_id, "reason": REASON},
        headers=demo.admin,
    )
    assert wrong.status_code == 422 and "active SME" in wrong.json()["detail"]
    assert demo.transfer(unit, ANITA, reason="").status_code == 422
    assert demo.transfer(unit, ANITA, expected_version=unit["version"] + 1).status_code == 409
    refused = c.post(
        f"{BASE}/units/{unit['id']}/transfer",
        json={"owner_id": demo.ids[ANITA], "reason": REASON},
        headers=demo.suresh,
    )
    assert refused.status_code == 403
    sent_before = len(demo.mail)

    moved = demo.transfer(unit, ANITA, expected_version=unit["version"])
    assert moved.status_code == 200, moved.text
    assert moved.json()["owner"]["id"] == demo.ids[ANITA]
    assert moved.json()["version"] == unit["version"] + 1
    assert moved.json()["counts"] == unit["counts"] | {"total": 1, "in_review": 1}

    # Suresh: the unit, its booklet and his old lock are gone; a write with it is refused.
    assert all(u["id"] != unit["id"] for u in demo.units(demo.suresh))
    assert c.get(f"{BASE}/booklets/{booklet}", headers=demo.suresh).status_code == 404
    stale = c.post(
        f"{BASE}/booklets/{booklet}/answers/{answer['id']}/approve",
        json={"expected_version": answer["version"]},
        headers=demo.suresh,
    )
    assert stale.status_code == 404
    # Anita opens it at once (no lock to wait for) and carries on.
    opened = c.post(f"{BASE}/booklets/{booklet}/lock", headers=demo.anita)
    assert opened.status_code == 200 and opened.json()["status"] == "in_review"
    approved = c.post(
        f"{BASE}/booklets/{booklet}/answers/{answer['id']}/approve",
        json={"expected_version": answer["version"]},
        headers=demo.anita,
    )
    assert approved.status_code == 200, approved.text

    history = c.get(f"{BASE}/units/{unit['id']}/transfers", headers=demo.admin).json()
    assert [(t["from_owner"]["id"], t["to_owner"]["id"], t["reason"]) for t in history] == [
        (demo.ids[SURESH], demo.ids[ANITA], REASON)
    ]
    assert c.get(f"{BASE}/units/{unit['id']}/transfers", headers=demo.anita).status_code == 403
    received, away = demo.mail[sent_before:]
    assert (received.to, away.to) == (ANITA, SURESH)
    assert all("leave" not in m.text for m in (received, away))


def test_the_inbox_lifecycle_over_http(demo: Demo) -> None:
    c = demo.c
    unit = demo.unit_of(SURESH)
    _entitled_to(demo, ANITA, unit["subject"]["id"])
    url = f"{BASE}/units/{unit['id']}/delegation-requests"
    made = c.post(url, json={"reason": "Exams clash"}, headers=demo.suresh)
    assert made.status_code == 201, made.text
    request = made.json()
    assert request["state"] == "pending" and request["unit"]["code"] == unit["code"]
    assert c.post(url, json={"reason": "Again"}, headers=demo.suresh).status_code == 409
    assert c.post(url, json={"reason": "Mine?"}, headers=demo.anita).status_code == 404
    assert c.post(url, json={"reason": "x"}, headers=demo.admin).status_code == 403
    listed = demo.units(demo.suresh)
    assert (
        next(u for u in listed if u["id"] == unit["id"])["pending_request"]["id"] == request["id"]
    )

    inbox = c.get(f"{BASE}/delegation-requests?state=pending", headers=demo.admin).json()
    assert [r["id"] for r in inbox] == [request["id"]]
    declined = c.post(
        f"{BASE}/delegation-requests/{request['id']}/decline",
        json={"note": "Nobody else is free"},
        headers=demo.admin,
    )
    assert declined.status_code == 200 and declined.json()["state"] == "declined"
    mine = c.get(f"{BASE}/delegation-requests", headers=demo.suresh).json()
    assert [(r["state"], r["note"]) for r in mine] == [("declined", "Nobody else is free")]
    assert c.get(f"{BASE}/delegation-requests", headers=demo.anita).json() == []

    second = c.post(url, json={"reason": "Still clashing"}, headers=demo.suresh).json()
    withdraw = f"{BASE}/delegation-requests/{second['id']}/withdraw"
    assert c.post(withdraw, headers=demo.anita).status_code == 404
    assert c.post(withdraw, headers=demo.suresh).json()["state"] == "withdrawn"
    assert c.post(withdraw, headers=demo.suresh).status_code == 409

    third = c.post(url, json={"reason": "Please"}, headers=demo.suresh).json()
    assert demo.transfer(unit, ANITA).status_code == 200
    states = {
        r["id"]: r["state"] for r in c.get(f"{BASE}/delegation-requests", headers=demo.admin).json()
    }
    assert states == {request["id"]: "declined", second["id"]: "withdrawn", third["id"]: "done"}
    # Suresh still sees his requests (the unit is Anita's now).
    seen = c.get(f"{BASE}/delegation-requests", headers=demo.suresh).json()
    assert seen[0]["id"] == third["id"] and seen[0]["unit"]["owner"]["id"] == demo.ids[ANITA]


def test_subject_removal_moves_open_units_or_nothing(demo: Demo) -> None:
    c = demo.c
    unit = demo.unit_of(SURESH)
    subject = unit["subject"]["id"]
    _entitled_to(demo, ANITA, subject)
    others = {s["id"] for s in c.get(f"{BASE}/subjects", headers=demo.admin).json()} - {subject}
    _entitled_to(demo, SURESH, sorted(others)[0])  # so the subject is not his last
    suresh = demo.ids[SURESH]
    open_units = demo.units(demo.admin, owner_id=suresh, subject_id=subject, state="open")
    assert unit["id"] in {u["id"] for u in open_units}

    plain = c.delete(f"{BASE}/accounts/{suresh}/subjects/{subject}", headers=demo.admin)
    assert plain.status_code == 409 and "open units" in plain.json()["detail"]
    revoke = f"{BASE}/accounts/{suresh}/subjects/{subject}/revoke"
    partial = c.post(revoke, json={"transfers": [], "reason": "x"}, headers=demo.admin)
    assert partial.status_code == 409
    every = [{"unit_id": u["id"], "owner_id": demo.ids[ANITA]} for u in open_units]
    done = c.post(revoke, json={"transfers": every, "reason": "Subject moved"}, headers=demo.admin)
    assert done.status_code == 200, done.text
    assert subject not in done.json()["subject_ids"]
    assert demo.units(demo.admin, owner_id=suresh, subject_id=subject, state="open") == []


def test_an_sme_with_open_units_is_not_removed(demo: Demo) -> None:
    c = demo.c
    suresh = demo.ids[SURESH]
    refused = c.delete(f"{BASE}/accounts/{suresh}", headers=demo.admin)
    assert refused.status_code == 409 and "transfer them" in refused.json()["detail"]
    assert c.delete(f"{BASE}/accounts/{suresh}", headers=demo.anita).status_code == 403
    for unit in demo.units(demo.admin, owner_id=suresh):
        _entitled_to(demo, ANITA, unit["subject"]["id"])
        assert demo.transfer(unit, ANITA).status_code == 200
    removed = c.delete(f"{BASE}/accounts/{suresh}", headers=demo.admin)
    assert removed.status_code == 204, removed.text
    listed = {a["user"]["id"] for a in c.get(f"{BASE}/accounts", headers=demo.admin).json()}
    assert suresh not in listed
    assert c.get(f"{BASE}/auth/me", headers=demo.suresh).status_code == 401
    login = c.post(
        f"{BASE}/auth/login",
        json={"institution_id": "DEMO_COM", "email": SURESH, "password": DEV_PASSWORD},
    )
    assert login.status_code == 401
    assert c.post(f"{BASE}/accounts/{suresh}/enable", headers=demo.admin).status_code == 404
    assert c.delete(f"{BASE}/accounts/{suresh}", headers=demo.admin).status_code == 404
