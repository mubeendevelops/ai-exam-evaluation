"""P21 item 1: every published endpoint, called by college B with college A's ids, on the
in-memory adapters (the same sweep on PostgreSQL + MinIO: ``test_api_tenancy_sweep_postgres``).
See ``tarn_api.tenancy_sweep`` for the classification of each operation."""

from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from tarn_adapters.config import Settings
from tarn_adapters.stages import LocalStageRunner
from tarn_adapters.testing import scripted_stages
from tarn_api.app import create_app
from tarn_api.tenancy_sweep import (
    B_INSTITUTION,
    CASES,
    World,
    build_world,
    call,
    invite_with_another_colleges_subject,
    login,
    smes_of_another_colleges_subject,
    unclassified,
    upload_for_another_college,
    url,
)
from tarn_api.testing import MemoryBackends
from tarn_core.ids import BookletId, CollegeId
from tarn_core.seed import DEV_PASSWORD
from tarn_core.testing.auth_world import AuthWorld
from tarn_core.testing.seed_world import seed_in_memory

EXPECTED = {"college": {404}}
B_GOVERNOR = "governor@demo-eng.example.test"


@pytest.fixture(scope="module")
def sweep() -> Iterator[tuple[World, MemoryBackends]]:
    backends = MemoryBackends()
    mem = backends.mem
    seed_in_memory(mem)
    app = create_app(
        Settings(_env_file=None, device="cpu", rate_limits_enabled=False), backends=backends
    )

    def process() -> None:
        assert (
            LocalStageRunner(scripted_stages(mem.blobs), mem).run(
                CollegeId(UUID(int=0)), BookletId(UUID(int=0))
            )
            == []
        )

    tenant = mem.identity.find_tenant(B_INSTITUTION)
    assert tenant is not None
    AuthWorld(mem).accounts.invite_governor(
        tenant.college_id, display_name="Sweep Governor", email=B_GOVERNOR, operator="sweep"
    )
    AuthWorld(mem).auth.accept_invite(
        tenant.college_id, mem.mailer.last_token(B_GOVERNOR), DEV_PASSWORD
    )
    with TestClient(app) as client:
        world = build_world(client, process)
        world.b_governor = login(client, B_INSTITUTION, B_GOVERNOR)
        yield world, backends


def test_every_published_operation_is_classified(sweep: tuple[World, MemoryBackends]) -> None:
    world, _ = sweep
    openapi = world.client.get("/openapi.json").json()
    assert unclassified(openapi) == []
    kinds = [case.kind for case in CASES.values()]
    assert kinds.count("college") >= 45


@pytest.mark.parametrize(
    ("method", "path"),
    [op for op, case in CASES.items() if case.kind != "own"],
    ids=lambda v: str(v),
)
def test_college_b_cannot_reach_college_a(
    sweep: tuple[World, MemoryBackends], method: str, path: str
) -> None:
    world, backends = sweep
    case = CASES[(method, path)]
    a_college = UUID(world.ids["a_college"])
    events = [e for e in backends.mem.audit.events if e.college_id == a_college]
    review = world.client.get(f"/api/v1/booklets/{world.ids['booklet_id']}/review", headers=world.a)
    response = call(world, method, path, case)
    assert response.status_code in EXPECTED[case.kind], (method, path, response.text)
    if case.kind == "college":
        assert response.json() == {"detail": "Not found."}  # indistinguishable from missing
    after = [e for e in backends.mem.audit.events if e.college_id == a_college]
    assert after == events, "college A's audit log changed"
    assert (
        world.client.get(
            f"/api/v1/booklets/{world.ids['booklet_id']}/review", headers=world.a
        ).json()
        == review.json()
    )


def test_b_cannot_register_a_booklet_for_a_student_of_a(
    sweep: tuple[World, MemoryBackends],
) -> None:
    world, _ = sweep
    assert upload_for_another_college(world).status_code == 404


def test_b_is_offered_nobody_for_a_subject_of_a(sweep: tuple[World, MemoryBackends]) -> None:
    world, _ = sweep
    response = smes_of_another_colleges_subject(world)
    assert response.status_code == 200 and response.json() == []
    c, b_admin = world.client, world.b_admin
    assert world.ids["unit_id"] not in c.get("/api/v1/units", headers=b_admin).text
    papers = c.get("/api/v1/units/papers", headers=b_admin).text
    assert world.ids["blueprint_id"] not in papers


def test_b_cannot_grant_a_subject_of_a(sweep: tuple[World, MemoryBackends]) -> None:
    world, _ = sweep
    response = invite_with_another_colleges_subject(world)
    assert response.status_code == 422 and "no such subject" in response.json()["detail"]
    accounts = world.client.get("/api/v1/accounts", headers=world.b_admin).json()
    assert "sweep.sme@demo-eng.example.test" not in {x["user"]["email"] for x in accounts}


def test_lists_show_only_the_callers_college(sweep: tuple[World, MemoryBackends]) -> None:
    world, _ = sweep
    c, b = world.client, world.b
    a_booklet = world.ids["booklet_id"]
    assert a_booklet not in c.get("/api/v1/booklets", headers=b).text
    assert a_booklet not in c.get("/api/v1/evaluated-booklets", headers=b).text
    a_student = world.ids["student_id"]
    a_students = c.get("/api/v1/students", params={"limit": 50}, headers=world.a_admin).json()
    names = {s["name"] for s in a_students}
    b_admin = world.b_admin
    b_students = c.get("/api/v1/students", params={"limit": 50}, headers=b_admin).json()
    assert a_student not in {s["id"] for s in b_students}
    for name in names:
        found = c.get("/api/v1/students", params={"q": name}, headers=b_admin).json()
        assert all(s["id"] != a_student for s in found)
    accounts = c.get("/api/v1/accounts", headers=world.b_admin).json()
    assert world.ids["user_id"] not in {x["user"]["id"] for x in accounts}


def test_content_lists_show_only_the_callers_college(sweep: tuple[World, MemoryBackends]) -> None:
    """Content is private to the college (D165): B's lists never name A's content."""
    world, _ = sweep
    c, a, b = world.client, world.a, world.b
    a_questions = c.get("/api/v1/questions", params={"limit": 100}, headers=a).json()["items"]
    b_questions = c.get("/api/v1/questions", params={"limit": 100}, headers=b).json()["items"]
    assert a_questions and b_questions
    assert not {q["id"] for q in a_questions} & {q["id"] for q in b_questions}
    for q in a_questions[:5]:
        found = c.get("/api/v1/questions", params={"code": q["code"]}, headers=b).json()
        assert all(x["id"] != q["id"] for x in found["items"])
    a_papers = {x["id"] for x in c.get("/api/v1/blueprints", headers=a).json()}
    b_papers = {x["id"] for x in c.get("/api/v1/blueprints", headers=b).json()}
    assert world.ids["blueprint_id"] in a_papers and not a_papers & b_papers
    a_subjects = {x["id"] for x in c.get("/api/v1/subjects", headers=a).json()}
    b_subjects = {x["id"] for x in c.get("/api/v1/subjects", headers=b).json()}
    assert a_subjects and b_subjects and not a_subjects & b_subjects
    a_topics = set(c.get("/api/v1/question-topics", headers=a).json())
    b_topics = set(c.get("/api/v1/question-topics", headers=b).json())
    assert a_topics != b_topics


def test_b_cannot_link_a_question_in_its_own_paper(sweep: tuple[World, MemoryBackends]) -> None:
    """A paper of B that names A's question id is refused like an unknown question."""
    world, _ = sweep
    c, b = world.client, world.b
    document = dict(world.blueprint_document)
    b_subject = c.get("/api/v1/subjects", headers=b).json()[0]["id"]
    document["subject_id"] = b_subject
    response = c.post("/api/v1/blueprints", json=document, headers=b)
    assert response.status_code == 422, response.text
    assert "not in your college" in response.json()["detail"]


# --- subjects inside one college (P24, D178) ----------------------------------------------------


@pytest.fixture(scope="module")
def physics(sweep: tuple[World, MemoryBackends]) -> dict[str, str]:
    """An SME of college A granted only a new Physics subject: A's commerce content and
    booklets are as missing to them as to college B."""
    world, backends = sweep
    c, admin = world.client, world.a_admin
    subject = c.post("/api/v1/subjects", json={"code": "PHY", "name": "Physics"}, headers=admin)
    assert subject.status_code == 201, subject.text
    email = "physics.sme@demo-com.example.test"
    invited = c.post(
        "/api/v1/accounts",
        json={"display_name": "Physics SME", "email": email, "subject_ids": [subject.json()["id"]]},
        headers=admin,
    )
    assert invited.status_code == 201 and invited.json()["subject_ids"] == [subject.json()["id"]]
    token = backends.mem.mailer.last_token(email)
    accepted = c.post(
        "/api/v1/auth/invitations/accept", json={"token": token, "password": DEV_PASSWORD}
    )
    assert accepted.status_code == 204, accepted.text
    return login(c, "DEMO_COM", email)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        op
        for op, case in CASES.items()
        if case.kind == "college" and not (case.as_admin or case.as_governor)
    ],
    ids=lambda v: str(v),
)
def test_a_physics_sme_finds_nothing_of_the_other_subjects(
    sweep: tuple[World, MemoryBackends], physics: dict[str, str], method: str, path: str
) -> None:
    world, _ = sweep
    case = CASES[(method, path)]
    extra = case.request(world) if case.request else {}
    headers = physics | extra.pop("headers", {})
    response = world.client.request(method, url(world, path), headers=headers, **extra)
    assert response.status_code == 404, (method, path, response.text)
    assert response.json() == {"detail": "Not found."}


def test_a_physics_sme_lists_nothing_of_the_other_subjects(
    sweep: tuple[World, MemoryBackends], physics: dict[str, str]
) -> None:
    world, _ = sweep
    c = world.client
    assert c.get("/api/v1/questions", headers=physics).json()["total"] == 0
    assert c.get("/api/v1/blueprints", headers=physics).json() == []
    assert c.get("/api/v1/booklets", headers=physics).json()["total"] == 0
    assert c.get("/api/v1/evaluated-booklets", headers=physics).json()["total"] == 0
    assert c.get("/api/v1/question-topics", headers=physics).json() == []
    assert [s["code"] for s in c.get("/api/v1/subjects", headers=physics).json()] == ["PHY"]
