"""Tests only. The cross-college sweep of P21 item 1: every endpoint the API publishes, called
by college B with college A's ids.

College data (booklets, pages, text, segments, answers, drawings, sheets, accounts, students)
and, since P23 (D165), the college's content (subjects, questions, keys, rubrics, glossaries,
key files, reference diagrams, papers) must look missing (404) to another college. ``CASES``
names what each published operation is; ``unclassified`` lists any operation the table does
not know, so a new route cannot slip past the sweep."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from tarn_core.seed import DEV_PASSWORD
from tarn_core.testing import fake_pdf

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

BASE = "/api/v1"
A_INSTITUTION, B_INSTITUTION = "DEMO_COM", "DEMO_ENG"
A_TEACHER = "suresh.naik@demo-com.example.test"
A_SECOND = "anita.desai@demo-com.example.test"
B_TEACHER = "ravi.menon@demo-eng.example.test"
B_ADMIN = "admin@demo-eng.example.test"
A_ADMIN = "admin@demo-com.example.test"
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)

Kind = Literal["college", "own"]
"""college: another college's row or content, 404 · own: acts on the caller's own college or
account only (no foreign id to try)."""


@dataclass(frozen=True, slots=True)
class Case:
    kind: Kind
    request: Callable[["World"], dict[str, Any]] | None = None
    """``client.request`` keyword arguments besides method and URL (body, params)."""
    as_admin: bool = False


@dataclass
class World:
    client: "TestClient"
    a: dict[str, str]
    """College A's SME (questions, papers, review)."""
    b: dict[str, str]
    """College B's SME."""
    b_admin: dict[str, str]
    a_admin: dict[str, str]
    ids: dict[str, str]
    """Path parameters (A's ids) and other values the requests need."""
    review: dict[str, Any]
    blueprint_document: dict[str, Any]


def login(client: "TestClient", institution: str, email: str) -> dict[str, str]:
    response = client.post(
        f"{BASE}/auth/login",
        json={"institution_id": institution, "email": email, "password": DEV_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def build_world(client: "TestClient", process: Callable[[], None]) -> World:
    """College A (demo commerce) with a booklet scored, approved and sheeted, a question with a
    key file and a reference diagram, its own blueprint; college B (demo engineering)."""
    c = client
    a = login(c, A_INSTITUTION, A_TEACHER)
    b = login(c, B_INSTITUTION, B_TEACHER)
    b_admin = login(c, B_INSTITUTION, B_ADMIN)
    a_admin = login(c, A_INSTITUTION, A_ADMIN)
    blueprint = next(
        x for x in c.get(f"{BASE}/blueprints", headers=a).json() if x["title"].startswith("QP-CI")
    )
    # The seed's QP-CI unit belongs to A's SME (P26).
    unit = next(
        u
        for u in c.get(f"{BASE}/units", headers=a_admin).json()
        if u["paper"]["id"] == blueprint["id"]
    )
    # A student without a booklet in the unit yet (a journey may have run first).
    taken = {
        b["student"]["id"]
        for b in c.get(f"{BASE}/units/{unit['id']}", headers=a_admin).json()["booklets"]
    }
    roster = c.get(f"{BASE}/students", params={"limit": 50}, headers=a_admin).json()
    student = next(s for s in roster if s["id"] not in taken)
    upload = c.post(
        f"{BASE}/units/{unit['id']}/booklets",
        headers=a_admin,
        files=[("files", (f"{student['usn']}.pdf", fake_pdf(1, "sweep"), "application/pdf"))],
    )
    assert upload.status_code == 200, upload.text
    booklet = upload.json()["files"][0]["booklet_id"]
    assert booklet, upload.text
    process()
    review = c.post(f"{BASE}/booklets/{booklet}/lock", headers=a).json()
    answers = [x for x in review["answers"] if x["attempted"] and x["suggestion"]]
    for x in answers:
        r = c.post(
            f"{BASE}/booklets/{booklet}/answers/{x['id']}/approve",
            json={"expected_version": x["version"]},
            headers=a,
        )
        assert r.status_code == 200, r.text
    review = c.get(f"{BASE}/booklets/{booklet}/review", headers=a).json()
    assert c.post(
        f"{BASE}/booklets/{booklet}/approve",
        json={"expected_version": review["version"]},
        headers=a,
    ).is_success
    # Locked again by A, so a write by B would otherwise meet 423 rather than 404.
    review = c.post(f"{BASE}/booklets/{booklet}/lock", headers=a).json()
    segments = c.get(f"{BASE}/booklets/{booklet}/segments", headers=a).json()["segments"]
    text = c.get(f"{BASE}/booklets/{booklet}/pages/1/text", headers=a).json()
    region = text["regions"][0]["id"]

    me = c.get(f"{BASE}/auth/me", headers=a).json()
    a_college = me["user"]["college_id"]
    question = c.get(f"{BASE}/questions", params={"limit": 5}, headers=a).json()["items"][0]
    qid = question["id"]
    key = c.post(
        f"{BASE}/questions/{qid}/key-files",
        params={"filename": "key.pdf", "confirm_no_student_data": True},
        content=b"%PDF-1.4 synthetic key",
        headers=a | {"Content-Type": "application/pdf"},
    )
    assert key.status_code == 201, key.text
    diagram = c.post(
        f"{BASE}/questions/{qid}/diagrams",
        params={"filename": "ref.png", "confirm_no_student_data": True},
        content=PNG_1PX,
        headers=a | {"Content-Type": "image/png"},
    )
    assert diagram.status_code == 201, diagram.text
    detail = c.get(f"{BASE}/questions/{qid}", headers=a).json()
    second = next(
        x["user"]["id"]
        for x in c.get(f"{BASE}/accounts", headers=a_admin).json()
        if x["user"]["email"] == A_SECOND
    )
    own_blueprint = c.get(f"{BASE}/blueprints/{blueprint['id']}", headers=a).json()
    return World(
        client=c,
        a=a,
        b=b,
        b_admin=b_admin,
        a_admin=a_admin,
        review=review,
        blueprint_document=own_blueprint["document"],
        ids={
            "booklet_id": booklet,
            "number": "1",
            "version": "1",
            "answer_id": answers[0]["id"],
            "region_id": region,
            "diagram_id": str(uuid4()),  # the booklet check comes first; no drawing needed
            "user_id": second,
            "blueprint_id": blueprint["id"],
            "question_id": qid,
            "file_id": key.json()["id"],
            "ref_diagram_id": diagram.json()["id"],
            "ref_answer_id": detail["reference_answers"][0]["id"],
            "student_id": student["id"],
            "student_usn": student["usn"],
            "unit_id": unit["id"],
            "subject_id": blueprint["subject_id"],
            "segment_a": segments[0]["id"],
            "segment_b": segments[-1]["id"],
            "a_college": a_college,
        },
    )


def _v(w: World) -> int:
    return int(w.review["version"])


def _answer_v(w: World) -> int:
    return int(next(x["version"] for x in w.review["answers"] if x["id"] == w.ids["answer_id"]))


def _json(body: Callable[[World], Any]) -> Callable[[World], dict[str, Any]]:
    return lambda w: {"json": body(w)}


CASES: Mapping[tuple[str, str], Case] = {
    # --- public or the caller's own account ----------------------------------------------
    ("GET", "/api/v1/health"): Case("own"),
    ("POST", "/api/v1/auth/login"): Case("own"),
    ("POST", "/api/v1/auth/refresh"): Case("own"),
    ("POST", "/api/v1/auth/logout"): Case("own"),
    ("GET", "/api/v1/auth/me"): Case("own"),
    ("POST", "/api/v1/auth/password/forgot"): Case("own"),
    ("POST", "/api/v1/auth/password/reset"): Case("own"),
    ("POST", "/api/v1/auth/password/recover"): Case("own"),
    ("POST", "/api/v1/auth/password/change"): Case("own"),
    ("POST", "/api/v1/auth/recovery-codes"): Case("own"),
    ("POST", "/api/v1/auth/invitations/accept"): Case("own"),
    ("GET", "/api/v1/auth/password-bloom"): Case("own"),
    ("GET", "/api/v1/registrations/availability"): Case("own"),
    ("POST", "/api/v1/registrations"): Case("own"),
    ("POST", "/api/v1/registrations/verify-email"): Case("own"),
    ("GET", "/api/v1/accounts"): Case("own"),
    ("POST", "/api/v1/accounts"): Case("own"),  # see ``invite_with_another_colleges_subject``
    ("POST", "/api/v1/roster/import"): Case("own"),
    ("GET", "/api/v1/students"): Case("own"),
    ("POST", "/api/v1/students"): Case("own"),
    ("GET", "/api/v1/subjects"): Case("own"),
    ("POST", "/api/v1/subjects"): Case("own"),
    ("POST", "/api/v1/blueprints/validate"): Case("own"),
    ("GET", "/api/v1/blueprints"): Case("own"),
    ("POST", "/api/v1/blueprints"): Case("own"),
    ("GET", "/api/v1/questions"): Case("own"),
    ("POST", "/api/v1/questions"): Case("own"),
    ("GET", "/api/v1/question-topics"): Case("own"),
    ("GET", "/api/v1/booklets"): Case("own"),
    ("GET", "/api/v1/evaluated-booklets"): Case("own"),
    ("GET", "/api/v1/units"): Case("own"),
    ("GET", "/api/v1/units/papers"): Case("own"),
    # A's subject: B is offered nobody (an empty list), see ``smes_of_another_colleges_subject``
    ("GET", "/api/v1/units/smes"): Case(
        "own", lambda w: {"params": {"subject_id": w.ids["subject_id"]}}
    ),
    # --- another college's units (P26) --------------------------------------------------------
    ("POST", "/api/v1/units"): Case(
        "college",
        _json(
            lambda w: {
                "paper_id": w.ids["blueprint_id"],
                "year": "Sweep",
                "owner_id": w.ids["user_id"],
                "due_date": "2030-01-01",
            }
        ),
        as_admin=True,
    ),
    ("GET", "/api/v1/units/{unit_id}"): Case("college", as_admin=True),
    ("POST", "/api/v1/units/{unit_id}/booklets"): Case(
        "college",
        lambda w: {
            "files": [
                ("files", (f"{w.ids['student_usn']}.pdf", fake_pdf(1, "b"), "application/pdf"))
            ]
        },
        as_admin=True,
    ),
    ("POST", "/api/v1/booklets/{booklet_id}/replace"): Case(
        "college",
        lambda w: {"files": [("file", ("r.pdf", fake_pdf(1, "b-retake"), "application/pdf"))]},
        as_admin=True,
    ),
    # --- another college's accounts ----------------------------------------------------------
    ("POST", "/api/v1/accounts/{user_id}/disable"): Case("college", as_admin=True),
    ("POST", "/api/v1/accounts/{user_id}/enable"): Case("college", as_admin=True),
    ("POST", "/api/v1/accounts/{user_id}/force-reset"): Case("college", as_admin=True),
    ("POST", "/api/v1/accounts/{user_id}/unlock"): Case("college", as_admin=True),
    ("GET", "/api/v1/accounts/{user_id}/subjects"): Case("college", as_admin=True),
    ("POST", "/api/v1/accounts/{user_id}/subjects/{subject_id}"): Case("college", as_admin=True),
    ("DELETE", "/api/v1/accounts/{user_id}/subjects/{subject_id}"): Case("college", as_admin=True),
    # --- another college's subjects (the admin's list, D160) ----------------------------------
    ("PUT", "/api/v1/subjects/{subject_id}"): Case(
        "college", _json(lambda w: {"code": "TAKEN", "name": "Taken over"}), as_admin=True
    ),
    ("POST", "/api/v1/subjects/{subject_id}/retire"): Case("college", as_admin=True),
    # --- another college's student (P25) -------------------------------------------------------
    ("PUT", "/api/v1/students/{student_id}"): Case(
        "college",
        _json(lambda w: {"name": "Taken Over", "usn": "TAKEN001", "class_section": ""}),
        as_admin=True,
    ),
    # --- another college's content (D165) ---------------------------------------------------
    ("GET", "/api/v1/blueprints/{blueprint_id}"): Case("college"),
    ("GET", "/api/v1/blueprints/{blueprint_id}/versions"): Case("college"),
    ("PUT", "/api/v1/blueprints/{blueprint_id}"): Case(
        "college", _json(lambda w: w.blueprint_document)
    ),
    ("GET", "/api/v1/questions/{question_id}"): Case("college"),
    ("GET", "/api/v1/questions/{question_id}/key-files/{file_id}/content"): Case("college"),
    ("GET", "/api/v1/questions/{question_id}/diagrams/{diagram_id}/content"): Case("college"),
    ("GET", "/api/v1/questions/{question_id}/diagrams/{diagram_id}/graph"): Case("college"),
    ("PUT", "/api/v1/questions/{question_id}"): Case(
        "college",
        _json(
            lambda w: {"code": "X-1", "text": "Taken over", "max_marks": 5, "difficulty": "easy"}
        ),
    ),
    ("PUT", "/api/v1/questions/{question_id}/rubric"): Case(
        "college", _json(lambda w: {"criteria": []})
    ),
    ("POST", "/api/v1/questions/{question_id}/reference-answers"): Case(
        "college", _json(lambda w: {"text": "Another college's key"})
    ),
    ("PUT", "/api/v1/questions/{question_id}/reference-answers/{answer_id}"): Case(
        "college", _json(lambda w: {"text": "Another college's key"})
    ),
    ("DELETE", "/api/v1/questions/{question_id}/reference-answers/{answer_id}"): Case("college"),
    ("PUT", "/api/v1/questions/{question_id}/glossary"): Case(
        "college", _json(lambda w: {"terms": []})
    ),
    ("POST", "/api/v1/questions/{question_id}/key-files"): Case(
        "college",
        lambda w: {
            "params": {"filename": "k.pdf", "confirm_no_student_data": True},
            "content": b"%PDF-1.4 other",
            "headers": {"Content-Type": "application/pdf"},
        },
    ),
    ("POST", "/api/v1/questions/{question_id}/diagrams"): Case(
        "college",
        lambda w: {
            "params": {"filename": "r.png", "confirm_no_student_data": True},
            "content": PNG_1PX,
            "headers": {"Content-Type": "image/png"},
        },
    ),
    ("POST", "/api/v1/questions/{question_id}/diagrams/{diagram_id}/graph/edits"): Case(
        "college", _json(lambda w: {"expected_version": 1, "edits": []})
    ),
    ("POST", "/api/v1/questions/{question_id}/diagrams/{diagram_id}/recognize"): Case("college"),
    # --- another college's booklet, by id in the path ---------------------------------------
    ("GET", "/api/v1/booklets/{booklet_id}"): Case("college"),
    ("DELETE", "/api/v1/booklets/{booklet_id}"): Case("college"),
    ("GET", "/api/v1/booklets/{booklet_id}/pages/{number}/image"): Case("college"),
    ("GET", "/api/v1/booklets/{booklet_id}/pages/{number}/text"): Case("college"),
    ("POST", "/api/v1/booklets/{booklet_id}/pages/{number}/use-anyway"): Case(
        "college", as_admin=True
    ),
    ("GET", "/api/v1/booklets/{booklet_id}/diagrams"): Case("college"),
    ("POST", "/api/v1/booklets/{booklet_id}/diagrams/{diagram_id}/edits"): Case(
        "college",
        _json(lambda w: {"expected_version": 1, "edits": [{"op": "remove_node", "id": "n1"}]}),
    ),
    ("GET", "/api/v1/booklets/{booklet_id}/answers/{answer_id}/diagram-comparisons"): Case(
        "college"
    ),
    ("GET", "/api/v1/booklets/{booklet_id}/result-sheets/{version}/pdf"): Case("college"),
    ("POST", "/api/v1/booklets/{booklet_id}/lock"): Case("college"),
    ("DELETE", "/api/v1/booklets/{booklet_id}/lock"): Case("college"),
    ("GET", "/api/v1/booklets/{booklet_id}/review"): Case("college"),
    ("GET", "/api/v1/booklets/{booklet_id}/result-sheets"): Case("college"),
    ("POST", "/api/v1/booklets/{booklet_id}/answers/{answer_id}/approve"): Case(
        "college", _json(lambda w: {"expected_version": _answer_v(w), "teacher_mark": 0})
    ),
    ("POST", "/api/v1/booklets/{booklet_id}/answers/{answer_id}/skip"): Case(
        "college", _json(lambda w: {"expected_version": _answer_v(w)})
    ),
    ("POST", "/api/v1/booklets/{booklet_id}/answers/{answer_id}/reopen"): Case(
        "college", _json(lambda w: {"expected_version": _answer_v(w), "reason": "x"})
    ),
    ("POST", "/api/v1/booklets/{booklet_id}/answers/{answer_id}/withdraw"): Case(
        "college", _json(lambda w: {"expected_version": _answer_v(w)})
    ),
    ("POST", "/api/v1/booklets/{booklet_id}/approve"): Case(
        "college", _json(lambda w: {"expected_version": _v(w)})
    ),
    ("POST", "/api/v1/booklets/{booklet_id}/regions/{region_id}"): Case(
        "college", _json(lambda w: {"expected_version": _v(w), "text": "overwritten"})
    ),
    ("GET", "/api/v1/booklets/{booklet_id}/segments"): Case("college"),
    ("POST", "/api/v1/booklets/{booklet_id}/segments/merge"): Case(
        "college",
        _json(
            lambda w: {
                "expected_version": _v(w),
                "first": w.ids["segment_a"],
                "second": w.ids["segment_b"],
            }
        ),
    ),
    ("POST", "/api/v1/booklets/{booklet_id}/segments/split"): Case(
        "college",
        _json(
            lambda w: {
                "expected_version": _v(w),
                "segment_id": w.ids["segment_a"],
                "at_region": w.ids["region_id"],
            }
        ),
    ),
    ("POST", "/api/v1/booklets/{booklet_id}/segments/reassign"): Case(
        "college",
        _json(lambda w: {"expected_version": _v(w), "segment_id": w.ids["segment_a"]}),
    ),
    ("POST", "/api/v1/booklets/{booklet_id}/segments/move-boundary"): Case(
        "college",
        _json(
            lambda w: {
                "expected_version": _v(w),
                "upper": w.ids["segment_a"],
                "lower": w.ids["segment_b"],
                "region": w.ids["region_id"],
            }
        ),
    ),
    ("POST", "/api/v1/booklets/{booklet_id}/segments/resegment"): Case(
        "college", _json(lambda w: {"expected_version": _v(w)})
    ),
}

# Path parameters whose name differs between the question and booklet families.
_RENAMES = {
    ("/questions/", "diagram_id"): "ref_diagram_id",
    ("/questions/", "answer_id"): "ref_answer_id",
}


def operations(openapi: Mapping[str, Any]) -> list[tuple[str, str]]:
    return [
        (method.upper(), path)
        for path, ops in openapi["paths"].items()
        for method in ops
        if method in ("get", "post", "put", "delete", "patch")
    ]


def unclassified(openapi: Mapping[str, Any]) -> list[tuple[str, str]]:
    return [op for op in operations(openapi) if op not in CASES]


def url(world: World, path: str) -> str:
    out = path
    for name in ("booklet_id", "number", "version", "answer_id", "region_id", "diagram_id"):
        key = name
        for (family, param), renamed in _RENAMES.items():
            if family in path and param == name:
                key = renamed
        out = out.replace("{" + name + "}", world.ids[key])
    for name in (
        "user_id",
        "blueprint_id",
        "question_id",
        "file_id",
        "subject_id",
        "student_id",
        "unit_id",
    ):
        out = out.replace("{" + name + "}", world.ids[name])
    assert "{" not in out, out
    return out


def call(world: World, method: str, path: str, case: Case) -> Any:
    headers = dict(world.b_admin if case.as_admin else world.b)
    extra = case.request(world) if case.request else {}
    headers |= extra.pop("headers", {})
    return world.client.request(method, url(world, path), headers=headers, **extra)


def invite_with_another_colleges_subject(world: World) -> Any:
    """B's admin invites an SME with A's subject: the subject is not B's, so it is refused
    (422 "no such subject", the subject is never named) and nobody is invited."""
    return world.client.post(
        f"{BASE}/accounts",
        headers=world.b_admin,
        json={
            "display_name": "Sweep SME",
            "email": "sweep.sme@demo-eng.example.test",
            "subject_ids": [world.ids["subject_id"]],
        },
    )


def upload_for_another_college(world: World) -> Any:
    """B's admin uploads A's student's roll number into B's own unit (if B has one) or into
    A's unit: A's unit is not found, and in B's college A's roll number is unknown."""
    return world.client.post(
        f"{BASE}/units/{world.ids['unit_id']}/booklets",
        headers=world.b_admin,
        files=[
            ("files", (f"{world.ids['student_usn']}.pdf", fake_pdf(1, "b-a"), "application/pdf"))
        ],
    )


def smes_of_another_colleges_subject(world: World) -> Any:
    """B's admin asks which SMEs may own a unit of A's subject: nobody (an empty list)."""
    return world.client.get(
        f"{BASE}/units/smes",
        headers=world.b_admin,
        params={"subject_id": world.ids["subject_id"]},
    )
