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
    CASES,
    World,
    build_world,
    call,
    unclassified,
    upload_for_another_college,
)
from tarn_api.testing import MemoryBackends
from tarn_core.ids import BookletId, CollegeId
from tarn_core.testing.seed_world import seed_in_memory

EXPECTED = {"college": {404}, "owner": {403}, "shared": {200, 201}}


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

    with TestClient(app) as client:
        yield build_world(client, process), backends


def test_every_published_operation_is_classified(sweep: tuple[World, MemoryBackends]) -> None:
    world, _ = sweep
    openapi = world.client.get("/openapi.json").json()
    assert unclassified(openapi) == []
    kinds = [case.kind for case in CASES.values()]
    assert kinds.count("college") >= 25 and kinds.count("owner") >= 10


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


def test_lists_show_only_the_callers_college(sweep: tuple[World, MemoryBackends]) -> None:
    world, _ = sweep
    c, b = world.client, world.b
    a_booklet = world.ids["booklet_id"]
    assert a_booklet not in c.get("/api/v1/booklets", headers=b).text
    assert a_booklet not in c.get("/api/v1/evaluated-booklets", headers=b).text
    a_student = world.ids["student_id"]
    a_students = c.get("/api/v1/students", params={"limit": 50}, headers=world.a).json()
    names = {s["name"] for s in a_students}
    b_students = c.get("/api/v1/students", params={"limit": 50}, headers=b).json()
    assert a_student not in {s["id"] for s in b_students}
    for name in names:
        found = c.get("/api/v1/students", params={"q": name}, headers=b).json()
        assert all(s["id"] != a_student for s in found)
    accounts = c.get("/api/v1/accounts", headers=world.b_admin).json()
    assert world.ids["user_id"] not in {x["user"]["id"] for x in accounts}
