"""Tests only. The persona sweep of P24 (like ``tenancy_sweep``): every operation the API
publishes, called by the admin, an SME and the Governor of one college, with that college's own
ids. ``ACCESS`` names what each operation needs; an operation it does not know fails the test,
so a new route cannot slip past the capability table (``tarn_core.domain.personas``).

- a set of capabilities: the persona may call it iff it has one of them (403 otherwise);
- ``public``: needs no sign-in (sign-in, registration, links, health);
- ``signed_in``: any signed-in persona, for their own account (me, logout, passwords).

Allowed calls must not be refused for the role (anything but 403); the request bodies are
valid, so a 403 means the capability check. Calls that remove things run last."""

from collections.abc import Callable, Mapping
from typing import Any, Literal

from tarn_api.tenancy_sweep import BASE, CASES, World, url
from tarn_core.domain.personas import Capability
from tarn_core.testing import fake_pdf

C = Capability
Access = frozenset[Capability] | Literal["public", "signed_in"]
PERSONAS = ("admin", "sme", "governor")


def _needs(*capabilities: Capability) -> frozenset[Capability]:
    return frozenset(capabilities)


_PUBLIC: Access = "public"
_SIGNED_IN: Access = "signed_in"
_SMES = _needs(C.MANAGE_SMES)
_SUBJECTS = _needs(C.MANAGE_SUBJECTS)
_STUDENTS = _needs(C.MANAGE_STUDENTS)
_UPLOAD = _needs(C.UPLOAD_BOOKLETS)
_PROGRESS = _needs(C.VIEW_PROGRESS)
_AUTHOR = _needs(C.AUTHOR_CONTENT)
_EVALUATE = _needs(C.EVALUATE)

ACCESS: Mapping[tuple[str, str], Access] = {
    # --- no sign-in, or the caller's own account ----------------------------------------------
    ("GET", "/api/v1/health"): _PUBLIC,
    ("POST", "/api/v1/auth/login"): _PUBLIC,
    ("POST", "/api/v1/auth/refresh"): _PUBLIC,
    ("POST", "/api/v1/auth/password/forgot"): _PUBLIC,
    ("POST", "/api/v1/auth/password/reset"): _PUBLIC,
    ("POST", "/api/v1/auth/password/recover"): _PUBLIC,
    ("POST", "/api/v1/auth/invitations/accept"): _PUBLIC,
    ("GET", "/api/v1/auth/password-bloom"): _PUBLIC,
    ("GET", "/api/v1/registrations/availability"): _PUBLIC,
    ("POST", "/api/v1/registrations"): _PUBLIC,
    ("POST", "/api/v1/registrations/verify-email"): _PUBLIC,
    ("POST", "/api/v1/auth/logout"): _SIGNED_IN,
    ("GET", "/api/v1/auth/me"): _SIGNED_IN,
    ("POST", "/api/v1/auth/password/change"): _SIGNED_IN,
    ("POST", "/api/v1/auth/recovery-codes"): _SIGNED_IN,
    # --- the admin: SMEs, subjects, students, uploads (D159, D160, D162) ----------------------
    ("GET", "/api/v1/accounts"): _SMES,
    ("POST", "/api/v1/accounts"): _SMES,
    ("POST", "/api/v1/accounts/{user_id}/disable"): _SMES,
    ("POST", "/api/v1/accounts/{user_id}/enable"): _SMES,
    ("POST", "/api/v1/accounts/{user_id}/force-reset"): _SMES,
    ("POST", "/api/v1/accounts/{user_id}/unlock"): _SMES,
    ("GET", "/api/v1/accounts/{user_id}/subjects"): _SMES,
    ("POST", "/api/v1/accounts/{user_id}/subjects/{subject_id}"): _SMES,
    ("DELETE", "/api/v1/accounts/{user_id}/subjects/{subject_id}"): _SMES,
    ("GET", "/api/v1/subjects"): _needs(C.MANAGE_SUBJECTS, C.AUTHOR_CONTENT),
    ("POST", "/api/v1/subjects"): _SUBJECTS,
    ("PUT", "/api/v1/subjects/{subject_id}"): _SUBJECTS,
    ("POST", "/api/v1/subjects/{subject_id}/retire"): _SUBJECTS,
    ("POST", "/api/v1/roster/import"): _STUDENTS,
    ("GET", "/api/v1/students"): _STUDENTS,
    ("POST", "/api/v1/students"): _STUDENTS,
    ("PUT", "/api/v1/students/{student_id}"): _STUDENTS,
    ("POST", "/api/v1/booklets"): _UPLOAD,
    # --- progress: admin and SME, no marks for the admin (D167) -------------------------------
    ("GET", "/api/v1/booklets"): _PROGRESS,
    ("GET", "/api/v1/booklets/{booklet_id}"): _PROGRESS,
    # --- the SME's content (D165, D178) -------------------------------------------------------
    ("POST", "/api/v1/blueprints/validate"): _AUTHOR,
    ("GET", "/api/v1/blueprints"): _AUTHOR,
    ("POST", "/api/v1/blueprints"): _AUTHOR,
    ("GET", "/api/v1/blueprints/{blueprint_id}"): _AUTHOR,
    ("PUT", "/api/v1/blueprints/{blueprint_id}"): _AUTHOR,
    ("GET", "/api/v1/blueprints/{blueprint_id}/versions"): _AUTHOR,
    ("GET", "/api/v1/questions"): _AUTHOR,
    ("POST", "/api/v1/questions"): _AUTHOR,
    ("GET", "/api/v1/question-topics"): _AUTHOR,
    ("GET", "/api/v1/questions/{question_id}"): _AUTHOR,
    ("PUT", "/api/v1/questions/{question_id}"): _AUTHOR,
    ("PUT", "/api/v1/questions/{question_id}/rubric"): _AUTHOR,
    ("POST", "/api/v1/questions/{question_id}/reference-answers"): _AUTHOR,
    ("PUT", "/api/v1/questions/{question_id}/reference-answers/{answer_id}"): _AUTHOR,
    ("DELETE", "/api/v1/questions/{question_id}/reference-answers/{answer_id}"): _AUTHOR,
    ("PUT", "/api/v1/questions/{question_id}/glossary"): _AUTHOR,
    ("POST", "/api/v1/questions/{question_id}/key-files"): _AUTHOR,
    ("GET", "/api/v1/questions/{question_id}/key-files/{file_id}/content"): _AUTHOR,
    ("POST", "/api/v1/questions/{question_id}/diagrams"): _AUTHOR,
    ("GET", "/api/v1/questions/{question_id}/diagrams/{diagram_id}/content"): _AUTHOR,
    ("GET", "/api/v1/questions/{question_id}/diagrams/{diagram_id}/graph"): _AUTHOR,
    ("POST", "/api/v1/questions/{question_id}/diagrams/{diagram_id}/graph/edits"): _AUTHOR,
    ("POST", "/api/v1/questions/{question_id}/diagrams/{diagram_id}/recognize"): _AUTHOR,
    # --- the SME's evaluation: pages, text, marks, reviews, sheets (never the admin, D167) ----
    ("DELETE", "/api/v1/booklets/{booklet_id}"): _EVALUATE,
    ("GET", "/api/v1/booklets/{booklet_id}/pages/{number}/image"): _EVALUATE,
    ("GET", "/api/v1/booklets/{booklet_id}/pages/{number}/text"): _EVALUATE,
    ("POST", "/api/v1/booklets/{booklet_id}/pages/{number}/use-anyway"): _EVALUATE,
    ("GET", "/api/v1/booklets/{booklet_id}/diagrams"): _EVALUATE,
    ("POST", "/api/v1/booklets/{booklet_id}/diagrams/{diagram_id}/edits"): _EVALUATE,
    ("GET", "/api/v1/booklets/{booklet_id}/answers/{answer_id}/diagram-comparisons"): _EVALUATE,
    ("GET", "/api/v1/evaluated-booklets"): _EVALUATE,
    ("GET", "/api/v1/booklets/{booklet_id}/result-sheets/{version}/pdf"): _EVALUATE,
    ("POST", "/api/v1/booklets/{booklet_id}/lock"): _EVALUATE,
    ("DELETE", "/api/v1/booklets/{booklet_id}/lock"): _EVALUATE,
    ("GET", "/api/v1/booklets/{booklet_id}/review"): _EVALUATE,
    ("GET", "/api/v1/booklets/{booklet_id}/result-sheets"): _EVALUATE,
    ("POST", "/api/v1/booklets/{booklet_id}/answers/{answer_id}/approve"): _EVALUATE,
    ("POST", "/api/v1/booklets/{booklet_id}/answers/{answer_id}/skip"): _EVALUATE,
    ("POST", "/api/v1/booklets/{booklet_id}/answers/{answer_id}/reopen"): _EVALUATE,
    ("POST", "/api/v1/booklets/{booklet_id}/answers/{answer_id}/withdraw"): _EVALUATE,
    ("POST", "/api/v1/booklets/{booklet_id}/approve"): _EVALUATE,
    ("POST", "/api/v1/booklets/{booklet_id}/regions/{region_id}"): _EVALUATE,
    ("GET", "/api/v1/booklets/{booklet_id}/segments"): _EVALUATE,
    ("POST", "/api/v1/booklets/{booklet_id}/segments/merge"): _EVALUATE,
    ("POST", "/api/v1/booklets/{booklet_id}/segments/split"): _EVALUATE,
    ("POST", "/api/v1/booklets/{booklet_id}/segments/reassign"): _EVALUATE,
    ("POST", "/api/v1/booklets/{booklet_id}/segments/move-boundary"): _EVALUATE,
    ("POST", "/api/v1/booklets/{booklet_id}/segments/resegment"): _EVALUATE,
}

MARKS_AND_TEXT = frozenset(
    op
    for op, access in ACCESS.items()
    if access == _EVALUATE or (op[0] == "GET" and access == _AUTHOR)
)
"""What D167 keeps from the admin: marks, answer text, reviews, sheets, and the keys."""

# Bodies of the operations the cross-college sweep calls "own" (it never needs one there).
_ROSTER = "name,usn,class/section\nPersona Student,PERSONA001,X\n"
_OWN_BODIES: Mapping[tuple[str, str], Callable[[World], dict[str, Any]]] = {
    ("POST", "/api/v1/accounts"): lambda w: {
        "json": {"display_name": "Persona SME", "email": "persona.sme@demo-com.example.test"}
    },
    ("POST", "/api/v1/subjects"): lambda w: {"json": {"code": "PERSONA", "name": "Persona"}},
    ("POST", "/api/v1/roster/import"): lambda w: {
        "content": _ROSTER,
        "headers": {"Content-Type": "text/csv"},
    },
    ("POST", "/api/v1/students"): lambda w: {
        "json": {"name": "Persona Student", "usn": "PERSONA002", "class_section": "X"}
    },
    ("PUT", "/api/v1/students/{student_id}"): lambda w: {
        "json": {"name": "Persona Student", "usn": "PERSONA003", "class_section": "X"}
    },
    ("POST", "/api/v1/blueprints/validate"): lambda w: {"json": w.blueprint_document},
    ("POST", "/api/v1/blueprints"): lambda w: {"json": w.blueprint_document},
    ("POST", "/api/v1/questions"): lambda w: {
        "json": {
            "subject_id": w.ids["subject_id"],
            "code": "PERSONA-Q1",
            "text": "A synthetic question.",
            "max_marks": 2,
            "difficulty": "easy",
        }
    },
    ("POST", "/api/v1/booklets"): lambda w: {
        "data": {"student_id": w.ids["student_id"], "blueprint_id": w.ids["blueprint_id"]},
        "files": [("files", ("p.pdf", fake_pdf(1, "persona"), "application/pdf"))],
    },
}

LAST = (
    ("DELETE", "/api/v1/accounts/{user_id}/subjects/{subject_id}"),
    ("POST", "/api/v1/subjects/{subject_id}/retire"),
    ("POST", "/api/v1/accounts/{user_id}/disable"),
    ("DELETE", "/api/v1/booklets/{booklet_id}"),
)


def operations(openapi: Mapping[str, Any]) -> list[tuple[str, str]]:
    return [
        (method.upper(), path)
        for path, ops in openapi["paths"].items()
        for method in ops
        if method in ("get", "post", "put", "delete", "patch")
    ]


def unclassified(openapi: Mapping[str, Any]) -> list[tuple[str, str]]:
    return [op for op in operations(openapi) if op not in ACCESS]


def checked() -> list[tuple[str, str]]:
    """The operations that need a capability, removals last."""
    ops = [op for op, access in ACCESS.items() if isinstance(access, frozenset)]
    return sorted(ops, key=lambda op: LAST.index(op) + 1 if op in LAST else 0)


def call(world: World, headers: dict[str, str], method: str, path: str) -> Any:
    op = (method, path)
    builder = _OWN_BODIES.get(op)
    if builder is None:
        case = CASES.get(op)
        builder = case.request if case is not None and case.request is not None else None
    extra = builder(world) if builder else {}
    headers = headers | extra.pop("headers", {})
    target = path if "{" not in path else url(world, path)
    if not target.startswith(BASE):
        target = BASE + target
    return world.client.request(method, target, headers=headers, **extra)
