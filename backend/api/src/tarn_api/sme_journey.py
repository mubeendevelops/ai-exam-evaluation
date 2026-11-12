"""Tests only. The SME portal's whole story over HTTP (P38 item 2), written once and run twice:
on the in-memory adapters (default suite) and on PostgreSQL + MinIO with the worker's runner
(integration), like ``tarn_api.admin_journey``, whose plumbing it reuses.

A brand-new college with three SMEs (two entitled to Compiler Theory, one to Databases). The
first SME builds a schema, generates a paper from it, links an existing question and a new one,
sets the exam code and previews the PDF. The admin creates a unit for that SME and uploads five
booklets. The SME is notified, lands on My Assignments, evaluates two booklets through the
student and question lists, and asks for delegation suggesting the second SME; the admin
transfers the unit to the suggested SME. The second SME finishes and submits; the reports and
the turnaround credit the second SME; the Governor freezes the unit; the paper is deleted; the
frozen record remains.

Along the way (P38 item 3): the search reaches only the caller's units and subjects; the
delegate list carries names of entitled colleagues only; the paper PDF never holds key text; a
paper with booklets refuses a structural change; no SME response carries another SME's email or
employee ID; no notice, email or audit event carries student data, answer text or secrets; the
delegation's audit event does not hold the reason."""

import copy
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import partial
from typing import Any

import pymupdf

from tarn_api.admin_journey import BASE, JOURNEY_PAGE, AdminJourney
from tarn_core.seed import DEV_PASSWORD
from tarn_core.testing import fake_pdf

INSTITUTION = "P38_JOURNEY"
DOMAIN = "p38-journey.example.test"
ADMIN = f"admin@{DOMAIN}"
GOVERNOR = f"governor@{DOMAIN}"
SMES = (
    # name, email, employee ID, department, subject
    ("First SME", f"first.sme@{DOMAIN}", "EMP-P38-0001", "Computer Science", "CT"),
    ("Second SME", f"second.sme@{DOMAIN}", "EMP-P38-0002", "Computer Science", "CT"),
    ("Third SME", f"third.sme@{DOMAIN}", "EMP-P38-0003", "Information Systems", "DB"),
)
EXAM_CODE = "CT-2026-MID"
YEAR = "2026"
STUDENTS = (
    ("Synthetic Learner One", "1JY26CT001"),
    ("Synthetic Learner Two", "1JY26CT002"),
    ("Synthetic Learner Three", "1JY26CT003"),
    ("Synthetic Learner Four", "1JY26CT004"),
    ("Synthetic Learner Five", "1JY26CT005"),
)
DELEGATION_REASON = "Exam duty clash this fortnight"
TRANSFER_REASON = "As the SME asked"
KEY_MARKERS = (
    # The reference answers and rubric statements: key text, never on the paper (D224).
    "Key: lexical, syntax and semantic analysis then code generation",
    "Key: identifiers with their type, scope and address",
    "reference statement on compiler phases",
    "reference statement on symbol tables",
)


def pdf_text(data: bytes) -> str:
    opened: Any = pymupdf.open  # PyMuPDF ships no types for the document API
    with opened(stream=data, filetype="pdf") as doc:
        return " ".join(" ".join(page.get_text().split()) for page in doc)


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, inner in value.items():
            yield str(key)
            yield from _strings(inner)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


@dataclass
class SmeJourney(AdminJourney):
    institution: str = INSTITUTION
    sme_bodies: dict[str, list[str]] = field(default_factory=dict)
    """Every response body each SME read, by email (scanned for the other SMEs' details)."""
    notices: list[dict[str, Any]] = field(default_factory=list)
    """Every notice any SME read."""
    headers: dict[str, dict[str, str]] = field(default_factory=dict)
    """Each SME's sign-in, by email."""

    def sme(self, email: str, method: str, path: str, status: int = 200, **kw: Any) -> Any:
        """A call by an SME, its body kept for the scans."""
        response = self.client.request(method, f"{BASE}{path}", headers=self.headers[email], **kw)
        assert response.status_code == status, (method, path, response.text)
        self.sme_bodies.setdefault(email, []).append(response.text)
        if response.headers.get("content-type", "").startswith("application/json"):
            return response.json() if response.content else None
        return response.content

    def inbox(self, email: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = self.sme(email, "GET", "/notifications")["items"]
        self.notices += items
        return items

    def search(self, email: str, q: str) -> dict[str, Any]:
        found: dict[str, Any] = self.sme(email, "GET", "/search", params={"q": q})
        return found

    def run(self) -> dict[str, Any]:
        c = self.client
        college, admin, governor = self.open_college(
            INSTITUTION, "P38 Journey College", ADMIN, GOVERNOR
        )

        # 1. The admin: two subjects, three SMEs with their profile fields, five students.
        subjects = {
            code: self.ok(
                c.post(f"{BASE}/subjects", json={"code": code, "name": name}, headers=admin), 201
            )
            for code, name in (("CT", "Compiler Theory"), ("DB", "Databases"))
        }
        ids: dict[str, str] = {}
        for name, email, employee_id, department, subject in SMES:
            body = {
                "display_name": name,
                "email": email,
                "employee_id": employee_id,
                "department": department,
                "subject_ids": [subjects[subject]["id"]],
            }
            created = self.audited(
                college,
                "account.created",
                lambda body=body: self.ok(  # type: ignore[misc]
                    c.post(f"{BASE}/accounts", json=body, headers=admin), 201
                ),
            )
            ids[email] = created["user"]["id"]
            self.accept(email)
            self.headers[email] = self.login(email)
        first, second, third = (email for _, email, *_ in SMES)
        for name, usn in STUDENTS:
            self.ok(
                c.post(
                    f"{BASE}/students",
                    json={"name": name, "usn": usn, "class_section": "CT-A"},
                    headers=admin,
                ),
                201,
            )
        me = self.sme(first, "GET", "/auth/me")["user"]
        assert (me["employee_id"], me["department"]) == ("EMP-P38-0001", "Computer Science")

        # 2. The first SME: an existing question in the bank, a schema of two 5-mark slots.
        ct = subjects["CT"]["id"]

        def question(code: str, text: str, key: str, statement: str) -> dict[str, Any]:
            created: dict[str, Any] = self.audited(
                college,
                "content.created",
                partial(
                    self.sme,
                    first,
                    "POST",
                    "/questions",
                    201,
                    json={
                        "subject_id": ct,
                        "code": code,
                        "text": text,
                        "max_marks": 5,
                        "difficulty": "medium",
                        "reference_answer": key,
                        "criteria": [
                            {
                                "type": "semantic",
                                "label": "Explains",
                                "weight": 5,
                                "params": {"reference_statement": statement},
                            }
                        ],
                    },
                ),
            )
            return created

        existing = question(
            "CT-Q1", "Explain the phases of a compiler.", KEY_MARKERS[0], KEY_MARKERS[2]
        )
        schema_doc = {
            "schema_version": "1.2",
            "kind": "schema",
            "title": "Compiler Theory mid-term matrix",
            "course_code": "CT",
            "subject_id": ct,
            "total_marks": 10,
            "mark_step": 0.5,
            "target_batch": "2026 batch",
            "weightage_note": "Theory 50 / application 50",
            "sections": [
                {
                    "label": "A",
                    "title": "Answer both",
                    "method": "semantic_rubric",
                    "choice": {"rule": "all"},
                    "items": [
                        {"type": "question", "label": str(n), "marks": 5, "question_id": None}
                        for n in (1, 2)
                    ],
                }
            ],
        }
        schema = self.audited(
            college,
            "content.created",
            partial(self.sme, first, "POST", "/blueprints", 201, json=schema_doc),
        )
        cards = self.sme(first, "GET", "/schemas")["items"]
        assert [s["id"] for s in cards] == [schema["id"]]
        assert self.sme(third, "GET", "/schemas")["items"] == []  # another subject's SME

        # 3. Generate a paper, link the existing question and a new one, set the exam code.
        paper = self.audited(
            college,
            "content.created",
            partial(self.sme, first, "POST", f"/blueprints/{schema['id']}/paper", 201, json={}),
        )
        assert paper["kind"] == "paper" and paper["document"]["from_schema"] == schema["id"]
        new = question("CT-Q2", "What does a symbol table hold?", KEY_MARKERS[1], KEY_MARKERS[3])
        document = copy.deepcopy(paper["document"])
        items = document["sections"][0]["items"]
        items[0]["question_id"], items[1]["question_id"] = existing["id"], new["id"]
        document["exam_code"] = EXAM_CODE
        document["descriptor"] = "Mid term 2026 batch"
        paper = self.audited(
            college,
            "content.edited",
            partial(self.sme, first, "PUT", f"/blueprints/{paper['id']}", json=document),
        )
        pid, version = paper["id"], paper["version"]
        listed = self.sme(first, "GET", "/papers")
        card = next(p for p in listed["items"] if p["id"] == pid)
        assert card["exam_code"] == EXAM_CODE and not card["in_use"]
        assert "Mid term 2026 batch" in listed["descriptors"]
        pdf = self.sme(first, "GET", f"/blueprints/{pid}/versions/{version}/paper.pdf")
        text = pdf_text(pdf)
        for wanted in (EXAM_CODE, existing["text"], new["text"], "Answer all questions."):
            assert wanted in text, wanted
        for key in KEY_MARKERS:
            assert key not in text, key
        assert c.get(f"{BASE}/blueprints/{pid}", headers=self.headers[third]).status_code == 404

        # 4. The admin creates the unit for the first SME and uploads five booklets.
        offered = self.admin_get(admin, "/units/smes", subject_id=ct)
        assert {s["id"] for s in offered} == {ids[first], ids[second]}
        unit = self.audited(
            college,
            "unit.created",
            lambda: self.ok(
                c.post(
                    f"{BASE}/units",
                    json={
                        "paper_id": pid,
                        "year": YEAR,
                        "owner_id": ids[first],
                        "due_date": "2030-06-30",
                    },
                    headers=admin,
                ),
                201,
            ),
        )
        uid = unit["id"]
        uploaded = self.audited(
            college,
            "unit.uploaded",
            lambda: self.ok(
                c.post(
                    f"{BASE}/units/{uid}/booklets",
                    headers=admin,
                    files=[
                        ("files", (f"{usn}.pdf", fake_pdf(1, f"p38-{usn}"), "application/pdf"))
                        for _, usn in STUDENTS
                    ],
                )
            ),
        )
        booklets = [f["booklet_id"] for f in uploaded["files"]]
        assert all(booklets), uploaded
        self.process()

        # 5. The structure is locked now (D222): a slot change is refused, a title is not.
        locked = self.sme(first, "GET", f"/blueprints/{pid}")
        assert locked["structure_locked"]
        moved = copy.deepcopy(locked["document"])
        moved["exam_code"] = "CT-2026-OTHER"
        refused = self.sme(first, "PUT", f"/blueprints/{pid}", 409, json=moved)
        assert "clone it to change the structure" in str(refused)
        retitled = copy.deepcopy(locked["document"])
        retitled["title"] = "Compiler Theory mid-term paper"
        self.sme(first, "PUT", f"/blueprints/{pid}", json=retitled)

        # 6. The first SME: notified, lands on My Assignments, finds the unit in the search.
        notices = self.inbox(first)
        assert [(n["kind"], n["unit_id"]) for n in notices] == [("assigned", uid)]
        self.sme(first, "POST", f"/notifications/{notices[0]['id']}/read")
        assert self.sme(first, "GET", "/notifications/unread-count")["unread"] == 0
        summary = self.sme(first, "GET", "/me/assignments/summary")
        assert (summary["units_lifetime"], summary["active"]) == (1, 1)
        assert [u["id"] for u in self.sme(first, "GET", "/units")] == [uid]
        found = self.search(first, EXAM_CODE)
        assert [u["unit_id"] for u in found["units"]] == [uid]
        assert [p["blueprint_id"] for p in found["papers"]] == [pid]
        student = STUDENTS[0][0]
        assert len(self.search(first, student)["students"]) == 1
        # The second SME shares the subject, not the unit; the third shares neither.
        theirs = self.search(second, EXAM_CODE)
        assert theirs["units"] == [] and [p["blueprint_id"] for p in theirs["papers"]] == [pid]
        assert self.search(second, student)["students"] == []
        assert self.search(third, EXAM_CODE) == {
            "units": [],
            "students": [],
            "papers": [],
            "schemas": [],
            "questions": [],
        }
        self.sme(second, "GET", f"/units/{uid}", 404)

        # 7. Evaluate through the student list and the question list: two booklets.
        rows = self.sme(first, "GET", f"/units/{uid}", params={"limit": 25})["booklets"]
        assert {r["id"] for r in rows} == set(booklets)
        assert {r["row_status"] for r in rows} == {"ready"}
        for booklet in booklets[:2]:
            review = self.sme(first, "GET", f"/booklets/{booklet}/review")
            codes = {a["question_code"] for a in review["answers"] if a["attempted"]}
            assert codes == {"CT-Q1", "CT-Q2"}, codes
            self.audited(
                college,
                "result_sheet.issued",
                partial(self.approve_all, self.headers[first], booklet),
            )
        rows = self.sme(first, "GET", f"/units/{uid}", params={"limit": 25})["booklets"]
        assert sorted(r["row_status"] for r in rows) == ["completed"] * 2 + ["ready"] * 3

        # 8. Delegate with a suggestion: entitled, active colleagues only, by name.
        delegates = self.sme(first, "GET", f"/units/{uid}/delegates")
        assert delegates == [{"id": ids[second], "name": "Second SME"}]
        before = len(self.events(college))
        request = self.audited(
            college,
            "delegation.requested",
            partial(
                self.sme,
                first,
                "POST",
                f"/units/{uid}/delegation-requests",
                201,
                json={"reason": DELEGATION_REASON, "suggested_to": ids[second]},
            ),
        )
        for event in self.events(college)[before:]:
            assert DELEGATION_REASON not in event.payload, event.action
        inbox = self.admin_get(admin, "/delegation-requests")
        asked = next(r for r in inbox if r["id"] == request["id"])
        assert asked["suggested"] == {"id": ids[second], "name": "Second SME", "available": True}
        self.audited(
            college,
            "unit.transferred",
            lambda: self.ok(
                c.post(
                    f"{BASE}/units/{uid}/transfer",
                    json={"owner_id": asked["suggested"]["id"], "reason": TRANSFER_REASON},
                    headers=admin,
                )
            ),
        )

        # 9. One notice each; the first SME has lost the unit, its students and its search.
        newest = self.inbox(first)[0]
        assert (newest["kind"], newest["unit_id"]) == ("request_done", uid)
        assert [(n["kind"], n["unit_id"]) for n in self.inbox(second)] == [("assigned", uid)]
        self.sme(first, "GET", f"/units/{uid}", 404)
        self.sme(first, "GET", f"/booklets/{booklets[0]}/review", 404)
        assert self.search(first, student)["students"] == []
        assert self.search(first, EXAM_CODE)["units"] == []

        # 10. The second SME finishes and submits.
        for booklet in booklets[2:]:
            self.audited(
                college,
                "result_sheet.issued",
                partial(self.approve_all, self.headers[second], booklet),
            )
        self.audited(
            college,
            "unit.submitted",
            partial(self.sme, second, "POST", f"/units/{uid}/submit", json={}),
        )

        # 11. The reports and the turnaround credit the second SME (D163), not the first.
        mine = self.sme(second, "GET", "/me/reports")
        assert mine["units_handed_in"] == 1 and mine["turnaround_lifetime"]["units"] == 1
        assert sum(q["units_submitted"] for q in mine["quarters"]) == 1
        assert sum(q["booklets_approved"] for q in mine["quarters"]) == len(booklets)
        assert (mine["open_units"], mine["booklets_pending"]) == (0, 0)
        summary = self.sme(second, "GET", "/me/assignments/summary")
        assert (summary["completed"], summary["turnaround_units"]) == (1, 1)
        assert summary["average_turnaround_days"] is not None
        theirs_report = self.sme(first, "GET", "/me/reports")
        assert theirs_report["units_handed_in"] == 0
        assert sum(q["booklets_approved"] for q in theirs_report["quarters"]) == 0
        assert self.sme(first, "GET", "/me/assignments/summary")["units_lifetime"] == 0

        # 12. The Governor freezes; the paper is deleted; the frozen record remains.
        self.audited(
            college,
            "unit.frozen",
            lambda: self.ok(c.post(f"{BASE}/units/{uid}/freeze", json={}, headers=governor)),
        )
        check = self.sme(second, "GET", f"/blueprints/{pid}/deletion")
        assert check == {"allowed": True, "blockers": []}
        self.audited(
            college,
            "content.deleted",
            partial(self.sme, second, "DELETE", f"/blueprints/{pid}", 204),
        )
        self.sme(second, "GET", f"/blueprints/{pid}", 404)
        assert pid not in str(self.sme(second, "GET", "/papers"))
        record = self.ok(c.get(f"{BASE}/units/{uid}/frozen-record", headers=governor))
        assert {b["booklet_id"] for b in record["booklets"]} == set(booklets)
        assert all(b["outcome"] == "approved" and b["sheets"] for b in record["booklets"])
        for booklet in booklets:
            for sheet in self.sme(second, "GET", f"/booklets/{booklet}/result-sheets"):
                got = c.get(sheet["pdf_url"], headers=self.headers[second])
                assert got.status_code == 200 and got.content.startswith(b"%PDF")

        # 13. The scans.
        self.scan(college)
        return {
            "college": college,
            "unit": uid,
            "booklets": booklets,
            "paper": pid,
            "second": ids[second],
        }

    def scan(self, college: Any) -> None:
        # No SME read another SME's email or employee ID (names only, D218, D225).
        for name, email, employee_id, *_ in SMES:
            for other, bodies in self.sme_bodies.items():
                if other == email:
                    continue
                for body in bodies:
                    assert email not in body and employee_id not in body, (other, name)
        # No student data, answer text or secret in a notice, an email or an audit event.
        texts = [line.lstrip("^> ") for line in JOURNEY_PAGE]
        names = [n for n, _ in STUDENTS]
        usns = [u for _, u in STUDENTS]
        banned = [v.lower() for v in (*texts, *names, *usns)]
        assert self.notices
        for notice in self.notices:
            allowed = {"id", "kind", "unit_id", "unit_code", "due_date", "request_id", "note"}
            assert set(notice) <= allowed | {"created_at", "read_at"}, notice
            body = " ".join(_strings(notice)).lower()
            assert not [v for v in banned if v in body], notice
        for message in self.mail.sent:
            body = (message.subject + "\n" + message.text).lower()
            assert not [v for v in banned if v in body], message.subject
        for event in self.events(college):
            lowered = event.payload.lower()
            assert DEV_PASSWORD.lower() not in lowered and "$argon2" not in lowered
            assert "token=" not in lowered
            assert not [v for v in banned if v in lowered], event.action
