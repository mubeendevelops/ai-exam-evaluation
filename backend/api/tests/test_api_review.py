"""The review over HTTP (P15): open (lock), decide answers, approve the booklet, amend with
result sheet v2, correct OCR text and segments, and the 409/423 refusals. In-memory backends;
the worker's re-score runs here with the trigram scorer."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from tarn_api.testing import MemoryBackends
from tarn_core.domain.booklet import Booklet
from tarn_core.domain.tenancy import Role, Student
from tarn_core.ids import AnswerId, CollegeId, StudentId
from tarn_core.ports.jobs import JOB_RESCORE_ANSWERS
from tarn_core.testing.auth_world import ADMIN_PASSWORD, TEACHER_PASSWORD, AuthWorld
from tarn_core.testing.builders import CollegeFixture, ci_shaped_blueprint
from tarn_core.testing.workflow import GOOD, HALF, booklet_scorer, scored_booklet

BASE = "/api/v1"


def _login(client: TestClient, institution: str, email: str, password: str) -> dict[str, str]:
    response = client.post(
        f"{BASE}/auth/login",
        json={"institution_id": institution, "email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@dataclass
class Setup:
    client: TestClient
    mem: Any
    teacher: dict[str, str]
    teacher2: dict[str, str]
    other: dict[str, str]
    college_id: CollegeId
    booklet: Booklet

    @property
    def url(self) -> str:
        return f"{BASE}/booklets/{self.booklet.id}"

    def review(self) -> dict[str, Any]:
        response = self.client.get(f"{self.url}/review", headers=self.teacher)
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    def answer(self, label: str) -> dict[str, Any]:
        return next(a for a in self.review()["answers"] if a["slot_label"] == label)

    def run_worker(self) -> None:
        """What the worker does for the queued ``answers.rescore`` jobs."""
        for job in [j for j in self.mem.jobs.jobs if j.kind == JOB_RESCORE_ANSWERS]:
            booklet_scorer(self.mem).rescore(
                self.college_id, None, [AnswerId(UUID(a)) for a in job.payload["answer_ids"]]
            )
        self.mem.jobs.jobs.clear()


@pytest.fixture
def s(client: TestClient, backends: MemoryBackends, world: AuthWorld) -> Setup:
    mem = backends.mem
    cid, admin = world.register("COLLEGE_A", "admin@a.example")
    world.invite(cid, admin, "teacher@a.example")
    world.invite(cid, admin, "teacher2@a.example")
    world.register("COLLEGE_B", "admin@b.example")
    users = list(mem.users.list(cid))
    student = Student(id=StudentId(mem.ids.new()), college_id=cid, name="S One", usn="TST001")
    mem.students.save(cid, student)
    fixture = CollegeFixture(
        college=mem.colleges.get(cid),
        teacher=next(u for u in users if u.email == "teacher@a.example"),
        admin=next(u for u in users if u.role is Role.ADMIN),
        students=(student,),
    )
    booklet = scored_booklet(
        mem, fixture, ci_shaped_blueprint(mem, fixture), {"1": GOOD, "2": HALF}
    )
    return Setup(
        client=client,
        mem=mem,
        teacher=_login(client, "COLLEGE_A", "teacher@a.example", TEACHER_PASSWORD),
        teacher2=_login(client, "COLLEGE_A", "teacher2@a.example", TEACHER_PASSWORD),
        other=_login(client, "COLLEGE_B", "admin@b.example", ADMIN_PASSWORD),
        college_id=cid,
        booklet=booklet,
    )


def test_review_approve_and_amend(s: Setup) -> None:
    c = s.client
    first = s.review()
    assert first["status"] == "scored" and first["lock"] is None
    one = s.answer("1")
    approve = {"expected_version": one["version"]}

    # Writes need the lock.
    refused = c.post(f"{s.url}/answers/{one['id']}/approve", json=approve, headers=s.teacher)
    assert refused.status_code == 423 and refused.json()["holder_id"] is None

    opened = c.post(f"{s.url}/lock", headers=s.teacher)
    assert opened.status_code == 200, opened.text
    view = opened.json()
    assert view["status"] == "in_review" and view["lock"]["mine"] is True
    assert view["lock"]["holder_name"] == "Teacher T"

    # A second teacher is kept out.
    locked = c.post(f"{s.url}/lock", headers=s.teacher2)
    assert locked.status_code == 423
    assert locked.json()["holder_id"] == view["lock"]["holder_id"]

    # Accept the AI mark on 1; override 2 with tags and remarks.
    done = c.post(f"{s.url}/answers/{one['id']}/approve", json=approve, headers=s.teacher)
    assert done.status_code == 200, done.text
    assert (
        c.post(f"{s.url}/answers/{one['id']}/approve", json=approve, headers=s.teacher).status_code
        == 409
    )  # stale version
    two = s.answer("2")
    assert two["suggestion"]["criteria"] and two["max_marks"] == 2.0
    body = {
        "expected_version": two["version"],
        "teacher_mark": 1.5,
        "tags": ["partial"],
        "remarks": "One item missing.",
    }
    after = c.post(f"{s.url}/answers/{two['id']}/approve", json=body, headers=s.teacher).json()
    approved_two = next(a for a in after["answers"] if a["id"] == two["id"])
    assert approved_two["approval"]["teacher_mark"] == 1.5
    assert approved_two["approval"]["ai_mark"] == two["suggestion"]["mark"]
    assert approved_two["approval"]["overridden"] is True
    assert after["can_approve"] is True and after["waiting"] == []

    # Booklet approval: sheet v1.
    sheet = c.post(
        f"{s.url}/approve", json={"expected_version": after["version"]}, headers=s.teacher
    )
    assert sheet.status_code == 200, sheet.text
    approved = sheet.json()
    assert approved["status"] == "approved" and [x["version"] for x in approved["sheets"]] == [1]

    # Amendment: reopen 2, correct its text, wait for the new suggestion, approve → v2.
    two = s.answer("2")
    reopened = c.post(
        f"{s.url}/answers/{two['id']}/reopen",
        json={"expected_version": two["version"], "reason": "Recount"},
        headers=s.teacher,
    ).json()
    assert reopened["status"] == "amendment_in_progress"
    assert reopened["approved"] is True and reopened["amendment_in_progress"] is True
    draft = next(a for a in reopened["answers"] if a["id"] == two["id"])
    assert draft["status"] == "suggested" and draft["draft"]["reason"] == "Recount"

    segments = c.get(f"{s.url}/segments", headers=s.teacher).json()
    region = next(seg["region_ids"][0] for seg in segments["segments"] if seg["slot_label"] == "2")
    edited = c.post(
        f"{s.url}/regions/{region}",
        json={"expected_version": reopened["version"], "text": "alpha and beta b."},
        headers=s.teacher,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["rescoring"] == [two["id"]]
    assert edited.json()["region"]["text"] == "alpha and beta b."
    pending = s.answer("2")
    assert pending["rescore_pending"] is True
    early = c.post(
        f"{s.url}/answers/{two['id']}/approve",
        json={"expected_version": pending["version"]},
        headers=s.teacher,
    )
    assert early.status_code == 409  # the new suggestion is on its way

    s.run_worker()
    fresh = s.answer("2")
    assert fresh["rescore_pending"] is False and fresh["version"] > pending["version"]
    final = c.post(
        f"{s.url}/answers/{two['id']}/approve",
        json={"expected_version": fresh["version"]},
        headers=s.teacher,
    ).json()
    assert final["status"] == "approved_amended"
    sheets = c.get(f"{s.url}/result-sheets", headers=s.teacher).json()
    assert [x["version"] for x in sheets] == [1, 2]
    assert sheets[1]["note"] == "2: Recount"

    # Closing releases the lock; the second teacher may open it now.
    assert c.delete(f"{s.url}/lock", headers=s.teacher).status_code == 204
    assert c.post(f"{s.url}/lock", headers=s.teacher2).status_code == 200


def test_segments_need_the_lock_and_the_version(s: Setup) -> None:
    c = s.client
    listed = c.get(f"{s.url}/segments", headers=s.teacher).json()
    first = listed["segments"][0]
    body = {
        "expected_version": listed["booklet_version"],
        "segment_id": first["id"],
        "label": None,
    }
    assert c.post(f"{s.url}/segments/reassign", json=body, headers=s.teacher).status_code == 423
    opened = c.post(f"{s.url}/lock", headers=s.teacher).json()
    stale = c.post(f"{s.url}/segments/reassign", json=body, headers=s.teacher)
    assert stale.status_code == 409  # opening moved the booklet on
    done = c.post(
        f"{s.url}/segments/reassign",
        json={**body, "expected_version": opened["version"]},
        headers=s.teacher,
    )
    assert done.status_code == 200, done.text
    out = done.json()
    assert out["booklet_version"] == opened["version"] + 1
    assert out["emptied"] and out["rescoring"] == []
    assert next(x for x in out["segments"] if x["id"] == first["id"])["slot_label"] is None


def test_another_college_sees_nothing(s: Setup) -> None:
    c = s.client
    assert c.get(f"{s.url}/review", headers=s.other).status_code == 404
    assert c.post(f"{s.url}/lock", headers=s.other).status_code == 404
    assert c.get(f"{s.url}/segments", headers=s.other).status_code == 404
    assert c.get(f"{s.url}/result-sheets", headers=s.other).status_code == 404


def test_deletion_waits_for_the_other_teachers_lock(s: Setup) -> None:
    c = s.client
    assert c.post(f"{s.url}/lock", headers=s.teacher).status_code == 200
    assert c.delete(s.url, headers=s.teacher2).status_code == 423
    assert c.delete(s.url, headers=s.teacher).status_code == 204
