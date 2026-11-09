"""The SME's evaluate flow over HTTP (P32, D217, D218): the student list of a unit (one call,
counts per booklet, filters, pages) and delegation with a suggested replacement that the admin
sees. The question list's codes are in ``test_api_review``."""

from collections.abc import Iterator
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from tarn_adapters.config import Settings
from tarn_api.app import create_app
from tarn_api.testing import MemoryBackends, queued_booklet
from tarn_core.domain.booklet import Answer, AnswerStatus, BookletStatus
from tarn_core.ids import AnswerId, BookletId, CollegeId, SegmentId, UserId
from tarn_core.seed import DEV_PASSWORD
from tarn_core.testing import fake_pdf
from tarn_core.testing.seed_world import seed_in_memory

BASE = "/api/v1"
ADMIN = "admin@demo-com.example.test"
SURESH = "suresh.naik@demo-com.example.test"
ANITA = "anita.desai@demo-com.example.test"
RAVI = "ravi.menon@demo-eng.example.test"


class Flow:
    def __init__(self, client: TestClient, backends: MemoryBackends) -> None:
        self.client = client
        self.mem = backends.mem
        self.admin = self.login("DEMO_COM", ADMIN)
        self.suresh = self.login("DEMO_COM", SURESH)
        self.anita = self.login("DEMO_COM", ANITA)
        self.ravi = self.login("DEMO_ENG", RAVI)
        me = client.get(f"{BASE}/auth/me", headers=self.suresh).json()["user"]
        self.suresh_id, self.cid = UUID(me["id"]), CollegeId(UUID(me["college_id"]))
        self.unit = next(
            u
            for u in client.get(f"{BASE}/units", headers=self.suresh).json()
            if u["paper"]["exam_code"] == "QP-CI-2021-06"
        )
        self.url = f"{BASE}/units/{self.unit['id']}"

    def login(self, institution: str, email: str) -> dict[str, str]:
        response = self.client.post(
            f"{BASE}/auth/login",
            json={"institution_id": institution, "email": email, "password": DEV_PASSWORD},
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def upload(self, usn: str) -> BookletId:
        return BookletId(
            UUID(
                queued_booklet(
                    self.client.post(
                        f"{self.url}/booklets",
                        headers=self.admin,
                        files=[("files", (f"{usn}.pdf", fake_pdf(1, usn), "application/pdf"))],
                    )
                )
            )
        )

    def move(self, booklet_id: BookletId, status: BookletStatus, *answers: AnswerStatus) -> None:
        """Puts the booklet where the test needs it (the machine stages are other tests')."""
        b = self.mem.booklets.get(self.cid, booklet_id)
        self.mem.booklets.save(self.cid, replace(b, status=status, version=b.version + 1))
        for n, state in enumerate(answers, start=1):
            self.mem.booklets.save_answer(
                self.cid,
                Answer(
                    id=AnswerId(uuid4()),
                    college_id=self.cid,
                    booklet_id=booklet_id,
                    slot_label=str(n),
                    segment_ids=(SegmentId(uuid4()),),
                    status=state,
                ),
            )

    def get(self, headers: dict[str, str], **params: Any) -> Any:
        response = self.client.get(self.url, headers=headers, params=params)
        assert response.status_code == 200, response.text
        return response.json()


@pytest.fixture
def flow() -> Iterator[Flow]:
    backends = MemoryBackends()
    seed_in_memory(backends.mem)
    app = create_app(
        Settings(_env_file=None, device="cpu", rate_limits_enabled=False), backends=backends
    )
    with TestClient(app) as client:
        yield Flow(client, backends)


def _students(flow: Flow) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = flow.client.get(
        f"{BASE}/students", params={"limit": 50}, headers=flow.admin
    ).json()
    return sorted(found, key=lambda s: s["usn"])


def test_the_student_list_counts_filters_and_pages(flow: Flow) -> None:
    first, second, third = _students(flow)[:3]
    done, review, waiting = (flow.upload(s["usn"]) for s in (first, second, third))
    flow.move(done, BookletStatus.APPROVED, AnswerStatus.APPROVED, AnswerStatus.APPROVED)
    flow.move(review, BookletStatus.IN_REVIEW, AnswerStatus.APPROVED, AnswerStatus.SKIPPED)

    body = flow.get(flow.suresh)
    assert body["matching"] == 3 and body["counts"]["total"] == 3
    rows = {r["student"]["usn"]: r for r in body["booklets"]}
    assert [r["student"]["usn"] for r in body["booklets"]] == [
        s["usn"] for s in (first, second, third)
    ]
    assert rows[first["usn"]]["row_status"] == "completed"
    assert rows[first["usn"]]["answers"] == {"attempted": 2, "approved": 2}
    assert rows[second["usn"]]["row_status"] == "in_review"
    assert rows[second["usn"]]["answers"] == {"attempted": 2, "approved": 1}
    assert rows[third["usn"]]["row_status"] == "processing"
    assert rows[third["usn"]]["answers"] == {"attempted": 0, "approved": 0}

    def usns(**params: Any) -> list[str]:
        return [r["student"]["usn"] for r in flow.get(flow.suresh, **params)["booklets"]]

    assert usns(q=second["usn"].lower()) == [second["usn"]]
    assert usns(q=third["name"].split()[0].upper()) == [
        s["usn"] for s in (first, second, third) if s["name"].split()[0] == third["name"].split()[0]
    ]
    assert usns(status="completed") == [first["usn"]]
    assert usns(status=["processing", "in_review"]) == [second["usn"], third["usn"]]
    paged = flow.get(flow.suresh, offset=1, limit=1)
    assert paged["matching"] == 3 and [r["id"] for r in paged["booklets"]] == [str(review)]
    for bad in ({"limit": 0}, {"limit": 101}, {"status": "verified"}, {"q": "x" * 81}):
        assert flow.client.get(flow.url, headers=flow.suresh, params=bad).status_code == 422

    # The admin's view: the same rows, row statuses, never the answers' tally (D167).
    admin = flow.get(flow.admin)
    assert {r["row_status"] for r in admin["booklets"]} == {"completed", "in_review", "processing"}
    assert all(r["answers"] is None for r in admin["booklets"])
    # Another SME's unit and another college's are not there.
    assert flow.client.get(flow.url, headers=flow.anita).status_code == 404
    assert flow.client.get(flow.url, headers=flow.ravi).status_code == 404
    assert waiting  # queued, untouched


def test_the_sme_suggests_a_replacement_the_admin_sees(flow: Flow) -> None:
    c = flow.client
    delegates = c.get(f"{flow.url}/delegates", headers=flow.suresh)
    assert delegates.status_code == 200
    (anita,) = delegates.json()
    assert anita["name"].startswith("Anita") and set(anita) == {"id", "name"}  # names only
    assert c.get(f"{flow.url}/delegates", headers=flow.anita).status_code == 404
    assert c.get(f"{flow.url}/delegates", headers=flow.ravi).status_code == 404
    assert c.get(f"{flow.url}/delegates", headers=flow.admin).status_code == 403

    ask = f"{flow.url}/delegation-requests"
    admin_id = c.get(f"{BASE}/auth/me", headers=flow.admin).json()["user"]["id"]
    ravi_id = c.get(f"{BASE}/auth/me", headers=flow.ravi).json()["user"]["id"]
    for who in (str(flow.suresh_id), admin_id, ravi_id, str(uuid4())):
        refused = c.post(ask, json={"reason": "On leave", "suggested_to": who}, headers=flow.suresh)
        assert refused.status_code == 422, who
    made = c.post(
        ask, json={"reason": "On leave", "suggested_to": anita["id"]}, headers=flow.suresh
    )
    assert made.status_code == 201, made.text
    assert made.json()["suggested"] == {**anita, "available": True}

    (request,) = c.get(f"{BASE}/delegation-requests", headers=flow.admin).json()
    assert request["suggested"] == {**anita, "available": True} and request["reason"] == "On leave"
    event = next(e for e in flow.mem.audit.events if e.action.value == "delegation.requested")
    assert isinstance(event.after, dict) and event.after["suggested_to"] == anita["id"]
    assert "On leave" not in repr(event)

    # Anita is disabled: the suggestion stays, no longer available to pre-fill.
    user = flow.mem.users.get(flow.cid, UserId(UUID(anita["id"])))
    flow.mem.users.save(flow.cid, replace(user, active=False))
    (request,) = c.get(f"{BASE}/delegation-requests", headers=flow.admin).json()
    assert request["suggested"]["available"] is False
    assert c.get(f"{flow.url}/delegates", headers=flow.suresh).json() == []
