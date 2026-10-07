"""Tests only. The teacher's whole journey over HTTP (P21 item 6), written once and run
twice: on the in-memory adapters (default suite) and on PostgreSQL + MinIO with the worker's runner
(integration). The caller supplies the client, how the queue is drained, and how to read the
college's audit log.

The demo seed's commerce college (``DEMO_COM``, exam QP-CI) is the stage: one teacher queues
five booklets (the sixth is refused), the queue drains one booklet at a time to ``scored``,
each booklet is opened, its answers approved (one overridden with tags and remarks) and the
booklet approved (sheet v1, PDF downloaded); a second teacher meets the lock; one booklet is
amended (sheet v2); the evaluated list shows the five; one booklet is deleted. Every write is
checked for its audit event, and the audit log for secrets and student text."""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any
from uuid import UUID

from tarn_core.seed import DEV_PASSWORD
from tarn_core.testing import fake_pdf

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

BASE = "/api/v1"
INSTITUTION = "DEMO_COM"
TEACHER = "suresh.naik@demo-com.example.test"
SECOND_TEACHER = "pooja.rao@demo-com.example.test"
QUEUE = 5


@dataclass(frozen=True, slots=True)
class Event:
    """An audit event as the journey sees it: the action and its before/after as JSON text."""

    action: str
    booklet_id: UUID | None
    payload: str


@dataclass
class Journey:
    client: "TestClient"
    process: Callable[[], None]
    """Drain the job queue (the worker)."""
    events: Callable[[UUID], Sequence[Event]]
    """The college's audit events, oldest first."""
    student_texts: Sequence[str] = ()
    """Answer text the pipeline read: must never appear in an audit event."""
    log: list[str] = field(default_factory=list)

    # --- plumbing ---------------------------------------------------------------------------

    def login(self, email: str) -> dict[str, str]:
        response = self.client.post(
            f"{BASE}/auth/login",
            json={"institution_id": INSTITUTION, "email": email, "password": DEV_PASSWORD},
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def audited(
        self, college: UUID, action: str, call: Callable[[], Any], *, count: int = 1
    ) -> Any:
        """Runs ``call`` and checks that it wrote at least ``count`` new ``action`` events."""
        before = len(self.events(college))
        result = call()
        new = [e.action for e in self.events(college)[before:]]
        assert new.count(action) >= count, f"{action} not audited; new events: {new}"
        self.log.append(action)
        return result

    # --- the journey ------------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        c = self.client
        t1, t2 = self.login(TEACHER), self.login(SECOND_TEACHER)
        me = c.get(f"{BASE}/auth/me", headers=t1).json()
        college = UUID(me["user"]["college_id"])
        students = c.get(f"{BASE}/students", params={"limit": 50}, headers=t1).json()
        assert len(students) >= QUEUE + 1
        blueprint = next(
            b
            for b in c.get(f"{BASE}/blueprints", headers=t1).json()
            if b["title"].startswith("QP-CI")
        )

        def upload(headers: dict[str, str], student: dict[str, Any], tag: str) -> Any:
            return c.post(
                f"{BASE}/booklets",
                headers=headers,
                data={"student_id": student["id"], "blueprint_id": blueprint["id"]},
                files=[("files", ("booklet.pdf", fake_pdf(1, tag), "application/pdf"))],
            )

        # 1. Five booklets queue; the sixth is refused; the same file again is a duplicate.
        booklets: list[str] = []
        for i in range(QUEUE):
            response = self.audited(
                college, "booklet.registered", partial(upload, t1, students[i], f"b{i}")
            )
            assert response.status_code == 201, response.text
            assert response.json()["status"] == "uploaded"
            booklets.append(response.json()["id"])
        listing = c.get(f"{BASE}/booklets", params={"mine": True}, headers=t1).json()
        assert listing["waiting"] == QUEUE == listing["max_waiting"]
        sixth = upload(t1, students[QUEUE], "b-sixth")
        assert sixth.status_code == 429, sixth.text
        assert upload(t2, students[0], "b0").status_code == 409  # duplicate, another teacher

        # 2. The worker drains the queue: every booklet reaches "scored".
        self.process()
        listing = c.get(f"{BASE}/booklets", params={"mine": True}, headers=t1).json()
        assert {b["id"]: b["status"] for b in listing["items"]} == dict.fromkeys(booklets, "scored")
        assert listing["waiting"] == 0
        actions = [e.action for e in self.events(college)]
        for done in ("booklet.processed", "booklet.text_read", "booklet.segmented"):
            assert actions.count(done) >= QUEUE, done
        assert actions.count("booklet.scored") >= QUEUE
        detail = c.get(f"{BASE}/booklets/{booklets[0]}", headers=t1).json()
        page = c.get(f"{BASE}{detail['pages'][0]['image_url'].removeprefix(BASE)}", headers=t1)
        assert page.status_code == 200 and page.headers["cache-control"] == "private, no-store"

        # 3. Each booklet: open, approve every answer, approve the booklet, download sheet v1.
        for n, booklet in enumerate(booklets):
            review = self.audited(
                college,
                "booklet.opened",
                partial(c.post, f"{BASE}/booklets/{booklet}/lock", headers=t1),
            ).json()
            assert review["status"] == "in_review" and review["lock"]["mine"]
            if n == 0:
                # The second teacher meets the lock: 423, nothing written.
                locked = c.post(f"{BASE}/booklets/{booklet}/lock", headers=t2)
                assert locked.status_code == 423, locked.text
            for i, answer in enumerate(review["answers"]):
                if not answer["attempted"] or answer["suggestion"] is None:
                    continue
                body: dict[str, Any] = {"expected_version": answer["version"]}
                if n == 0 and i == 0:
                    body |= {
                        "teacher_mark": 0,
                        "tags": ["Needs more detail"],
                        "remarks": "Overridden in the journey test.",
                    }
                approved = self.audited(
                    college,
                    "answer.approved",
                    partial(
                        c.post,
                        f"{BASE}/booklets/{booklet}/answers/{answer['id']}/approve",
                        json=body,
                        headers=t1,
                    ),
                )
                assert approved.status_code == 200, approved.text
            review = c.get(f"{BASE}/booklets/{booklet}/review", headers=t1).json()
            assert review["can_approve"], review["waiting"]
            done = self.audited(
                college,
                "result_sheet.issued",
                partial(
                    c.post,
                    f"{BASE}/booklets/{booklet}/approve",
                    json={"expected_version": review["version"]},
                    headers=t1,
                ),
            )
            assert done.status_code == 200, done.text
            sheets = c.get(f"{BASE}/booklets/{booklet}/result-sheets", headers=t1).json()
            assert [s["version"] for s in sheets] == [1]
            pdf = c.get(f"{BASE}{sheets[0]['pdf_url'].removeprefix(BASE)}", headers=t1)
            assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
            if n == 0:
                first = c.get(f"{BASE}/booklets/{booklet}/review", headers=t1).json()["answers"]
                overridden = next(a for a in first if a["approval"])
                assert overridden["approval"]["teacher_mark"] == 0
                assert overridden["approval"]["remarks"] == "Overridden in the journey test."

        # 4. An amendment: reopen an approved answer, approve it again: sheet v2; v1 stays.
        target = booklets[0]
        review = c.post(f"{BASE}/booklets/{target}/lock", headers=t1).json()
        answer = next(a for a in review["answers"] if a["approval"])
        reopened = self.audited(
            college,
            "amendment.opened",
            lambda: c.post(
                f"{BASE}/booklets/{target}/answers/{answer['id']}/reopen",
                json={"expected_version": answer["version"], "reason": "Recount"},
                headers=t1,
            ),
        )
        assert reopened.status_code == 200, reopened.text
        again = next(
            a
            for a in c.get(f"{BASE}/booklets/{target}/review", headers=t1).json()["answers"]
            if a["id"] == answer["id"]
        )
        amended = self.audited(
            college,
            "result_sheet.issued",
            lambda: c.post(
                f"{BASE}/booklets/{target}/answers/{answer['id']}/approve",
                json={"expected_version": again["version"], "teacher_mark": 1},
                headers=t1,
            ),
        )
        assert amended.status_code == 200, amended.text
        sheets = c.get(f"{BASE}/booklets/{target}/result-sheets", headers=t1).json()
        assert [s["version"] for s in sheets] == [1, 2]
        for sheet in sheets:
            pdf = c.get(f"{BASE}{sheet['pdf_url'].removeprefix(BASE)}", headers=t1)
            assert pdf.status_code == 200
        self.audited(
            college,
            "booklet.closed",
            lambda: c.delete(f"{BASE}/booklets/{target}/lock", headers=t1),
        )

        # 5. The evaluated list holds all five; the second teacher sees them too (A3.2).
        evaluated = c.get(f"{BASE}/evaluated-booklets", headers=t2).json()
        assert {b["id"] for b in evaluated["items"]} == set(booklets)
        statuses = {b["id"]: b["status"] for b in evaluated["items"]}
        assert statuses[target] == "approved_amended"

        # 6. Deletion: the booklet and its files go; one content-free record stays.
        gone = booklets[-1]
        deleted = self.audited(
            college, "booklet.deleted", lambda: c.delete(f"{BASE}/booklets/{gone}", headers=t1)
        )
        assert deleted.status_code == 204
        assert c.get(f"{BASE}/booklets/{gone}", headers=t1).status_code == 404
        record = [e for e in self.events(college) if e.action == "booklet.deleted"]
        assert len(record) == 1 and str(record[0].booklet_id) == gone

        # 7. The audit log holds no secret and no student text.
        texts = [*self.student_texts, *(s["name"] for s in students), *(s["usn"] for s in students)]
        for event in self.events(college):
            lowered = event.payload.lower()
            assert DEV_PASSWORD.lower() not in lowered
            assert "token=" not in lowered and "$argon2" not in lowered
            for text in texts:
                assert text.lower() not in lowered, (event.action, text)
        return {
            "college": college,
            "booklets": booklets,
            "deleted": gone,
            "headers": (t1, t2),
            "students": students,
        }


def event_payload(before: object, after: object) -> str:
    return json.dumps({"before": before, "after": after}, default=str, sort_keys=True)
