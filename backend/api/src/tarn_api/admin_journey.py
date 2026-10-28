"""Tests only. The admin portal's whole story over HTTP (P30 item 2), written once and run twice:
on the in-memory adapters (default suite) and on PostgreSQL + MinIO with the worker's runner
(integration), like ``tarn_api.journey``.

A brand-new college: the Tarn operator approves its registration and creates its Governor; the
admin adds subjects, two SMEs and the students; the first SME writes two questions and a paper
with an exam code; the admin creates a unit and uploads five booklets, one named by a roll
number the roster has wrong, fixed in Students and uploaded again; the first SME marks some; the
admin transfers the unit; the second SME finishes and submits; the admin returns it; the SME
amends one answer and resubmits; the Governor freezes it; the SME deletes the paper; the frozen
record and every result sheet remain.

Along the way: every write is checked for its audit event; every response the admin reads is
checked for marks, answer text and reviews (D167); every email and audit event for student
names, roll numbers, answer text and secrets."""

import json
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any
from uuid import UUID

from tarn_api.backends import Backends, unit_of_work
from tarn_api.journey import Event
from tarn_core.ids import CollegeId
from tarn_core.seed import DEV_PASSWORD
from tarn_core.testing import MemoryMailer, fake_pdf

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

BASE = "/api/v1"
INSTITUTION = "P30_JOURNEY"
DOMAIN = "p30-journey.example.test"
ADMIN = f"admin@{DOMAIN}"
GOVERNOR = f"governor@{DOMAIN}"
SME_1 = f"first.sme@{DOMAIN}"
SME_2 = f"second.sme@{DOMAIN}"
OPERATOR = "p30-operator"
EXAM_CODE = "CT-2024-HY"
YEAR = "2024"

JOURNEY_PAGE = (
    "^ Section A",
    "> 1. a compiler translates the whole source program into target code",
    "lexical analysis syntax analysis semantic analysis and code generation",
    "> 2. a symbol table stores identifiers with their type and scope",
    "the parser and the semantic analyser read and update it",
)
"""What the scripted OCR reads on every page: answers to the journey paper's two questions
(the line syntax of ``tarn_core.testing.segmentation``)."""

STUDENTS = (
    ("Synthetic Student One", "1JY24CT001"),
    ("Synthetic Student Two", "1JY24CT002"),
    ("Synthetic Student Three", "1JY24CT003"),
    ("Synthetic Student Four", "1JY24CT004"),
    ("Synthetic Student Five", "1JY24CT005"),
)
MISTYPED = "1JY24CT05"
"""The fifth student's roll number as first typed into the roster (a digit missing)."""

TRANSFER_REASON = "First SME on leave"
RETURN_NOTE = "Recheck question 2 against the key"

# Keys the admin may never see in a response (D167): marks, credits, suggestions, answer text,
# reviews and sheets. Allowed although they match: ``total_marks`` (the paper's structure, out
# of how many), ``in_review`` (a count of booklets), ``needs_text_pages``, ``text_read`` and
# ``needs_text`` (page numbers and flags of the processing status), ``unmarked`` (a submission's
# booklets left unmarked, with their reasons: the admin's to read, D205). The page links are allowed
# only empty: the admin gets no page image, original or text (D177; a flagged page's image while
# a retake is pending is P26's one exception, not met in this journey).
ADMIN_BANNED = ("mark", "score", "credit", "suggest", "grade", "review", "sheet", "text", "answer")
ADMIN_ALLOWED = frozenset(
    {"total_marks", "in_review", "needs_text_pages", "text_read", "needs_text", "unmarked"}
)
ADMIN_EMPTY = frozenset({"text_url", "image_url", "original_url", "pdf_url"})


def _items(value: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, inner in value.items():
            yield str(key), inner
            yield from _items(inner)
    elif isinstance(value, list):
        for item in value:
            yield from _items(item)


@dataclass
class AdminJourney:
    client: "TestClient"
    backends: Backends
    """For the operator's steps (``tarn tenants approve`` / ``governor`` do the same)."""
    process: Callable[[], None]
    """Drain the job queue (the worker)."""
    events: Callable[[UUID], Sequence[Event]]
    """The college's audit events, oldest first."""
    mail: MemoryMailer
    log: list[str] = field(default_factory=list)
    admin_reads: list[str] = field(default_factory=list)
    """Every path the admin read (checked for marks)."""

    # --- plumbing ---------------------------------------------------------------------------

    def login(self, email: str) -> dict[str, str]:
        response = self.client.post(
            f"{BASE}/auth/login",
            json={"institution_id": INSTITUTION, "email": email, "password": DEV_PASSWORD},
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def audited(self, college: UUID, action: str, call: Callable[[], Any]) -> Any:
        before = len(self.events(college))
        result = call()
        new = [e.action for e in self.events(college)[before:]]
        assert action in new, f"{action} not audited; new events: {new}"
        self.log.append(action)
        return result

    def ok(self, response: Any, status: int = 200) -> Any:
        assert response.status_code == status, (response.request.url, response.text)
        return response.json() if response.content else None

    def admin_get(self, admin: dict[str, str], path: str, **params: Any) -> Any:
        """A read by the admin: never a mark, a credit, a suggestion, answer text or a sheet."""
        body = self.ok(self.client.get(f"{BASE}{path}", headers=admin, params=params))
        banned = [
            k
            for k, v in _items(body)
            if (k in ADMIN_EMPTY and v is not None)
            or (
                k not in ADMIN_ALLOWED | ADMIN_EMPTY
                and any(word in k.lower() for word in ADMIN_BANNED)
            )
        ]
        assert banned == [], (path, banned)
        for booklet in _booklets(body):
            assert booklet.get("result") is None, (path, booklet)
        self.admin_reads.append(path)
        return body

    def accept(self, email: str) -> None:
        token = self.mail.last_token(email)
        accepted = self.client.post(
            f"{BASE}/auth/invitations/accept", json={"token": token, "password": DEV_PASSWORD}
        )
        assert accepted.status_code == 204, accepted.text

    def approve_all(self, headers: dict[str, str], booklet: str) -> Any:
        """Open, approve every suggested answer, approve the booklet (sheet v1)."""
        c = self.client
        review = self.ok(c.post(f"{BASE}/booklets/{booklet}/lock", headers=headers))
        for answer in review["answers"]:
            if answer["attempted"] and answer["suggestion"] is not None and not answer["approval"]:
                self.ok(
                    c.post(
                        f"{BASE}/booklets/{booklet}/answers/{answer['id']}/approve",
                        json={"expected_version": answer["version"]},
                        headers=headers,
                    )
                )
        review = self.ok(c.get(f"{BASE}/booklets/{booklet}/review", headers=headers))
        assert review["can_approve"], review["waiting"]
        return self.ok(
            c.post(
                f"{BASE}/booklets/{booklet}/approve",
                json={"expected_version": review["version"]},
                headers=headers,
            )
        )

    # --- the journey ------------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        c = self.client

        # 1. The operator: the college registers, verifies its email, is approved; its
        #    Governor is created and accepts the invitation.
        registered = c.post(
            f"{BASE}/registrations",
            json={
                "institution_id": INSTITUTION,
                "institution_name": "P30 Journey College",
                "admin_name": "Shared Admin",
                "email": ADMIN,
                "password": DEV_PASSWORD,
            },
        )
        assert registered.status_code == 202, registered.text
        verified = self.ok(
            c.post(
                f"{BASE}/registrations/verify-email", json={"token": self.mail.last_token(ADMIN)}
            )
        )
        assert verified["status"] == "PENDING_APPROVAL", verified
        assert c.post(  # not before the operator approves
            f"{BASE}/auth/login",
            json={"institution_id": INSTITUTION, "email": ADMIN, "password": DEV_PASSWORD},
        ).status_code in (401, 403)
        with self.backends.identity() as store:
            tenant = store.find_tenant(INSTITUTION)
        assert tenant is not None
        college = CollegeId(tenant.college_id)
        with unit_of_work(self.backends, college) as unit:
            unit.registration.approve(college, operator=OPERATOR)
        with unit_of_work(self.backends, college) as unit:
            unit.accounts.invite_governor(
                college, display_name="Journey Governor", email=GOVERNOR, operator=OPERATOR
            )
        self.accept(GOVERNOR)
        actions = [e.action for e in self.events(college)]
        assert "tenant.approved" in actions and "account.governor_invited" in actions
        admin, governor = self.login(ADMIN), self.login(GOVERNOR)

        # 2. The admin: two subjects, two SMEs entitled to the first, the students.
        subject = self.audited(
            college,
            "content.created",
            lambda: self.ok(
                c.post(
                    f"{BASE}/subjects",
                    json={"code": "CT", "name": "Compiler Theory"},
                    headers=admin,
                ),
                201,
            ),
        )
        self.ok(
            c.post(f"{BASE}/subjects", json={"code": "DB", "name": "Databases"}, headers=admin),
            201,
        )
        smes: list[dict[str, str]] = []
        for name, email in (("First SME", SME_1), ("Second SME", SME_2)):
            self.audited(
                college,
                "account.created",
                lambda name=name, email=email: self.ok(  # type: ignore[misc]
                    c.post(
                        f"{BASE}/accounts",
                        json={
                            "display_name": name,
                            "email": email,
                            "subject_ids": [subject["id"]],
                        },
                        headers=admin,
                    ),
                    201,
                ),
            )
            self.accept(email)
            smes.append(self.login(email))
        sme1, sme2 = smes
        accounts = self.admin_get(admin, "/accounts")
        ids = {a["user"]["email"]: a["user"]["id"] for a in accounts}
        roles = {a["user"]["email"]: a["user"]["role"] for a in accounts}
        assert roles == {ADMIN: "admin", GOVERNOR: "governor", SME_1: "sme", SME_2: "sme"}
        students: list[dict[str, Any]] = []
        for i, (name, usn) in enumerate(STUDENTS):
            typed = MISTYPED if i == len(STUDENTS) - 1 else usn
            students.append(
                self.audited(
                    college,
                    "student.added",
                    lambda name=name, typed=typed: self.ok(  # type: ignore[misc]
                        c.post(
                            f"{BASE}/students",
                            json={"name": name, "usn": typed, "class_section": "CT-A"},
                            headers=admin,
                        ),
                        201,
                    ),
                )
            )

        # 3. The first SME writes two questions and the paper, with an exam code.
        questions = []
        for n, (text, statement) in enumerate(
            (
                ("Explain the phases of a compiler.", "the phases of a compiler"),
                ("What does a symbol table hold?", "identifiers with type and scope"),
            ),
            start=1,
        ):
            questions.append(
                self.audited(
                    college,
                    "content.created",
                    lambda n=n, text=text, statement=statement: self.ok(  # type: ignore[misc]
                        c.post(
                            f"{BASE}/questions",
                            json={
                                "subject_id": subject["id"],
                                "code": f"CT-Q{n}",
                                "text": text,
                                "max_marks": 5,
                                "difficulty": "medium",
                                "reference_answer": statement,
                                "criteria": [
                                    {
                                        "type": "semantic",
                                        "label": "Explains",
                                        "weight": 5,
                                        "params": {"reference_statement": statement},
                                    }
                                ],
                            },
                            headers=sme1,
                        ),
                        201,
                    ),
                )
            )
        document = {
            "schema_version": "1.1",
            "title": "Compiler Theory half-yearly",
            "course_code": "CT",
            "exam_code": EXAM_CODE,
            "subject_id": subject["id"],
            "duration_minutes": 90,
            "total_marks": 10,
            "negative_marking": 0,
            "mark_step": 0.5,
            "sections": [
                {
                    "label": "A",
                    "title": "Answer both",
                    "method": "keyword_formula",
                    "choice": {"rule": "all"},
                    "items": [
                        {"type": "question", "label": str(n), "marks": 5, "question_id": q["id"]}
                        for n, q in enumerate(questions, start=1)
                    ],
                }
            ],
        }
        paper = self.audited(
            college,
            "content.created",
            lambda: self.ok(c.post(f"{BASE}/blueprints", json=document, headers=sme1), 201),
        )
        assert c.post(f"{BASE}/blueprints", json=document, headers=admin).status_code == 403

        # 4. The admin creates the unit for the first SME (only entitled SMEs are offered).
        papers = self.admin_get(admin, "/units/papers")
        offered = next(p for p in papers if p["id"] == paper["id"])
        assert offered["exam_code"] == EXAM_CODE
        entitled = self.admin_get(admin, "/units/smes", subject_id=subject["id"])
        assert {s["id"] for s in entitled} == {ids[SME_1], ids[SME_2]}
        unit = self.audited(
            college,
            "unit.created",
            lambda: self.ok(
                c.post(
                    f"{BASE}/units",
                    json={
                        "paper_id": paper["id"],
                        "year": YEAR,
                        "owner_id": ids[SME_1],
                        "due_date": "2030-06-30",
                    },
                    headers=admin,
                ),
                201,
            ),
        )
        uid = unit["id"]
        assigned = [m for m in self.mail.sent if m.to == SME_1 and unit["code"] in m.subject]
        assert assigned, "the SME is emailed on assignment"

        def upload(*usns: str) -> Any:
            return self.audited(
                college,
                "unit.uploaded",
                lambda: self.ok(
                    c.post(
                        f"{BASE}/units/{uid}/booklets",
                        headers=admin,
                        files=[
                            ("files", (f"{usn}.pdf", fake_pdf(1, f"p30-{usn}"), "application/pdf"))
                            for usn in usns
                        ],
                    )
                ),
            )

        # Five files; the fifth names a roll number the roster has wrong.
        first = upload(*(usn for _, usn in STUDENTS))
        outcomes = [(f["outcome"], f["problem"]) for f in first["files"]]
        assert outcomes == [("queued", None)] * 4 + [("refused", "unknown_usn")], outcomes
        assert "Students" in first["files"][-1]["message"]
        fifth = students[-1]
        self.audited(
            college,
            "student.edited",
            lambda: self.ok(
                c.put(
                    f"{BASE}/students/{fifth['id']}",
                    json={"name": fifth["name"], "usn": STUDENTS[-1][1], "class_section": "CT-A"},
                    headers=admin,
                )
            ),
        )
        again = upload(STUDENTS[-1][1])
        assert [f["outcome"] for f in again["files"]] == ["queued"]
        booklets = [f["booklet_id"] for f in first["files"][:4]] + [again["files"][0]["booklet_id"]]
        assert c.post(f"{BASE}/units/{uid}/booklets", headers=sme1, files=[]).status_code in (
            403,
            422,
        )
        self.process()
        detail = self.admin_get(admin, f"/units/{uid}")
        assert {b["id"]: b["status"] for b in detail["booklets"]} == dict.fromkeys(
            booklets, "scored"
        )
        self.admin_get(admin, f"/booklets/{booklets[0]}")
        for path in ("review", "result-sheets", "pages/1/text", "segments"):
            refused = c.get(f"{BASE}/booklets/{booklets[0]}/{path}", headers=admin)
            assert refused.status_code == 403, (path, refused.text)

        # 5. The first SME marks two booklets and opens a third; the second SME sees nothing.
        for booklet in booklets[:2]:
            self.audited(college, "result_sheet.issued", partial(self.approve_all, sme1, booklet))
        opened = self.ok(c.post(f"{BASE}/booklets/{booklets[2]}/lock", headers=sme1))
        assert opened["lock"]["mine"]
        assert c.get(f"{BASE}/units/{uid}", headers=sme2).status_code == 404
        assert c.get(f"{BASE}/booklets/{booklets[0]}/review", headers=sme2).status_code == 404
        progress = self.admin_get(admin, "/overview/progress")
        mine = next(s for s in progress["smes"] if s["user_id"] == ids[SME_1])
        assert mine["approved"] == 2 and mine["pending"] == 3, mine
        self.admin_get(admin, "/overview")

        # 6. The admin transfers the whole unit: locks go, both SMEs are emailed.
        sent = len(self.mail.sent)
        moved = self.audited(
            college,
            "unit.transferred",
            lambda: self.ok(
                c.post(
                    f"{BASE}/units/{uid}/transfer",
                    json={"owner_id": ids[SME_2], "reason": TRANSFER_REASON},
                    headers=admin,
                )
            ),
        )
        assert moved["owner"]["id"] == ids[SME_2]
        notices = self.mail.sent[sent:]
        assert {m.to for m in notices} == {SME_1, SME_2}
        assert all(TRANSFER_REASON not in m.text for m in notices)  # the reason stays inside
        assert c.get(f"{BASE}/units/{uid}", headers=sme1).status_code == 404
        assert c.get(f"{BASE}/booklets/{booklets[0]}/review", headers=sme1).status_code == 404
        transfers = self.admin_get(admin, f"/units/{uid}/transfers")
        assert len(transfers) == 1
        progress = self.admin_get(admin, "/overview/progress")
        credit = {s["user_id"]: s for s in progress["smes"]}
        assert credit[ids[SME_2]]["approved"] == 2 and credit[ids[SME_1]]["booklets"] == 0

        # 7. The second SME finishes (the third booklet's lock was freed) and submits.
        for booklet in booklets[2:]:
            self.audited(college, "result_sheet.issued", partial(self.approve_all, sme2, booklet))
        submitted = self.audited(
            college,
            "unit.submitted",
            lambda: self.ok(c.post(f"{BASE}/units/{uid}/submit", json={}, headers=sme2)),
        )
        assert submitted["state"] == "submitted"
        locked = c.post(f"{BASE}/booklets/{booklets[0]}/lock", headers=sme2)
        assert locked.status_code == 409, locked.text
        assert [u["id"] for u in self.ok(c.get(f"{BASE}/units", headers=governor))] == [uid]

        # The admin returns it with a note; the SME amends one answer (sheet v2), resubmits.
        returned = self.audited(
            college,
            "unit.returned",
            lambda: self.ok(
                c.post(f"{BASE}/units/{uid}/return", json={"note": RETURN_NOTE}, headers=admin)
            ),
        )
        assert returned["state"] == "open"
        assert RETURN_NOTE in self.mail.sent[-1].text and self.mail.sent[-1].to == SME_2
        target = booklets[0]
        review = self.ok(c.post(f"{BASE}/booklets/{target}/lock", headers=sme2))
        answer = next(a for a in review["answers"] if a["approval"])
        self.audited(
            college,
            "amendment.opened",
            lambda: self.ok(
                c.post(
                    f"{BASE}/booklets/{target}/answers/{answer['id']}/reopen",
                    json={"expected_version": answer["version"], "reason": "Student query"},
                    headers=sme2,
                )
            ),
        )
        again_answer = next(
            a
            for a in self.ok(c.get(f"{BASE}/booklets/{target}/review", headers=sme2))["answers"]
            if a["id"] == answer["id"]
        )
        self.audited(
            college,
            "result_sheet.issued",
            lambda: self.ok(
                c.post(
                    f"{BASE}/booklets/{target}/answers/{answer['id']}/approve",
                    json={"expected_version": again_answer["version"], "teacher_mark": 2},
                    headers=sme2,
                )
            ),
        )
        self.ok(c.delete(f"{BASE}/booklets/{target}/lock", headers=sme2), 204)
        self.audited(
            college,
            "unit.submitted",
            lambda: self.ok(c.post(f"{BASE}/units/{uid}/submit", json={}, headers=sme2)),
        )

        # 8. The Governor freezes and signs off.
        frozen = self.audited(
            college,
            "unit.frozen",
            lambda: self.ok(c.post(f"{BASE}/units/{uid}/freeze", json={}, headers=governor)),
        )
        assert frozen["state"] == "frozen"
        assert (
            c.post(f"{BASE}/units/{uid}/return", json={"note": "x"}, headers=admin).status_code
            == 409
        )

        # 9. The SME deletes the paper (allowed once its unit is frozen, D166).
        check = self.ok(c.get(f"{BASE}/blueprints/{paper['id']}/deletion", headers=sme2))
        assert check == {"allowed": True, "blockers": []}
        self.audited(
            college,
            "content.deleted",
            lambda: self.ok(c.delete(f"{BASE}/blueprints/{paper['id']}", headers=sme2), 204),
        )
        assert c.get(f"{BASE}/blueprints/{paper['id']}", headers=sme2).status_code == 404

        # 10. What remains: the frozen record and every sheet PDF; the admin's unit view.
        record = self.ok(c.get(f"{BASE}/units/{uid}/frozen-record", headers=governor))
        assert record["unit_code"] == unit["code"] and record["papers"]
        assert {b["booklet_id"] for b in record["booklets"]} == set(booklets)
        assert all(b["outcome"] == "approved" and b["sheets"] for b in record["booklets"])
        amended = next(b for b in record["booklets"] if b["booklet_id"] == target)
        assert [s["version"] for s in amended["sheets"]] == [1, 2]
        evaluated = self.ok(c.get(f"{BASE}/evaluated-booklets", headers=sme2))["items"]
        assert {e["id"] for e in evaluated} == set(booklets)
        for item in evaluated:
            for sheet in item["sheets"]:
                pdf = c.get(sheet["pdf_url"], headers=sme2)
                assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
        assert self.admin_get(admin, f"/units/{uid}")["state"] == "frozen"
        self.admin_get(admin, "/units")
        self.admin_get(admin, "/booklets")
        self.admin_get(admin, "/students")
        self.admin_get(admin, "/overview")

        # 11. No student data in any email or audit event; no secret in the audit log.
        texts = [*(line.lstrip("^> ") for line in JOURNEY_PAGE)]
        names = [name for name, _ in STUDENTS]
        usns = [usn for _, usn in STUDENTS] + [MISTYPED]
        for message in self.mail.sent:
            body = (message.subject + "\n" + message.text).lower()
            for value in (*texts, *names, *usns):
                assert value.lower() not in body, (message.subject, value)
        for event in self.events(college):
            lowered = event.payload.lower()
            assert DEV_PASSWORD.lower() not in lowered and "$argon2" not in lowered
            assert "token=" not in lowered
            for value in (*texts, *names, *usns):
                assert value.lower() not in lowered, (event.action, value)
        return {
            "college": college,
            "unit": uid,
            "booklets": booklets,
            "paper": paper["id"],
            "amended": target,
        }


def _booklets(body: Any) -> Iterator[dict[str, Any]]:
    """The booklet rows in a response (lists, ``items`` and a unit's ``booklets``)."""
    rows: list[Any] = []
    if isinstance(body, dict):
        if "status" in body and "student" in body:
            rows.append(body)
        rows += [x for key in ("items", "booklets") for x in body.get(key) or []]
    elif isinstance(body, list):
        rows += body
    for row in rows:
        if isinstance(row, dict) and "status" in row and "student" in row:
            yield row


def event_payload(before: object, after: object) -> str:
    return json.dumps({"before": before, "after": after}, default=str, sort_keys=True)
