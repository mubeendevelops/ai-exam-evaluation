"""The development seed on PostgreSQL (``make up``, then ``make test-integration``): twice in
a row changes nothing, each college sees only its own people and roster, and the seeded
teachers can sign in and find their questions through the API."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tarn_adapters.config import Settings
from tarn_adapters.postgres.database import PostgresDatabase
from tarn_adapters.postgres.testing import TestDatabase
from tarn_adapters.runtime import SystemClock, UuidGenerator
from tarn_adapters.seed import asset_reader, run_seed
from tarn_api.app import create_app
from tarn_api.backends import PostgresBackends
from tarn_core.domain.content import Question, ReferenceAnswer
from tarn_core.seed import COMMERCE, DEV_PASSWORD, ENGINEERING, SeedError
from tarn_core.testing import InMemory

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def settings(
    test_database: TestDatabase,
    identity_test_database: TestDatabase,
    tmp_path_factory: pytest.TempPathFactory,
) -> Settings:
    keys: Path = tmp_path_factory.mktemp("seed-keys")
    return Settings(
        app_database_url=test_database.app_url,
        identity_app_database_url=identity_test_database.app_url,
        local_key_dir=keys,
        device="cpu",
    )


def test_every_seed_asset_is_a_png() -> None:
    read = asset_reader()
    names = {
        "k-ai1-q2a-unit.png",
        "k-ai1-q2b-network.png",
        "k-ai1-q5-dendrogram.png",
        "k-ai1-q7a-vector-form.png",
        "k-ai1-q8a-rnn.png",
        "k-ai3-q4-model-based-agent.png",
        "k-ai3-q4-utility-based-agent.png",
    }
    for name in names:
        assert read(name).startswith(b"\x89PNG\r\n\x1a\n"), name
    with pytest.raises(SeedError):
        read("../data/../__init__.py")


def test_seed_twice_then_use_it(settings: Settings) -> None:
    blobs = InMemory().blobs
    first = run_seed(settings, blobs=blobs)
    assert first.report.created["colleges"] == 2
    assert first.report.created["questions"] == 63
    assert first.report.created["blueprints"] == 4

    second = run_seed(settings, blobs=blobs)
    assert second.report.created == {}
    assert second.report.kept["questions"] == 63
    assert {a.college_id for _, a in first.colleges} == {a.college_id for _, a in second.colleges}

    # row-level security: each college sees only its own users and roster
    app = PostgresDatabase(settings.app_database_url)
    try:
        by_name = {c.institution_id: a for c, a in second.colleges}
        eng, com = by_name[ENGINEERING.institution_id], by_name[COMMERCE.institution_id]
        ids, clock = UuidGenerator(), SystemClock()
        with app.session(eng.college_id, ids=ids, clock=clock, blobs=blobs) as scope:
            assert len(scope.users.list(eng.college_id)) == 3
            assert {s.usn[:5] for s in scope.students.list(eng.college_id)} == {"DEMOE"}
            owned = [
                q
                for q in scope.content.latest(Question)
                if q.meta.owning_college_id == eng.college_id
            ]
            assert len(owned) == 28
            synthetic = [
                a
                for q in scope.content.latest(Question)
                for a in scope.content.for_question(ReferenceAnswer, q.id)
                if a.synthetic
            ]
            assert synthetic  # the commerce keys are visible to every college, labelled
        with app.session(com.college_id, ids=ids, clock=clock, blobs=blobs) as scope:
            assert {s.usn[:5] for s in scope.students.list(com.college_id)} == {"DEMOC"}
    finally:
        app.dispose()

    # the seven reference diagrams were stored from the packaged files
    assert first.report.created["reference diagrams"] == 7


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    backends = PostgresBackends(settings)
    try:
        with TestClient(create_app(settings, backends=backends)) as c:
            yield c
    finally:
        backends.dispose()


def test_seeded_teachers_sign_in_and_see_their_bank(settings: Settings, client: TestClient) -> None:
    run_seed(settings, blobs=InMemory().blobs)
    for college, teacher, owned in (
        (ENGINEERING, ENGINEERING.teachers[0], 28),
        (COMMERCE, COMMERCE.teachers[1], 35),
    ):
        login = client.post(
            "/api/v1/auth/login",
            json={
                "institution_id": college.institution_id,
                "email": teacher.email,
                "password": DEV_PASSWORD,
            },
        )
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        mine = client.get("/api/v1/questions?mine=true&limit=100", headers=headers).json()
        assert mine["total"] == owned
        blueprints = client.get("/api/v1/blueprints", headers=headers).json()
        titles = {
            b["title"]
            for b in (blueprints if isinstance(blueprints, list) else blueprints["items"])
        }
        assert "QP-CI Constitution of India and Human Rights, June 2021" in titles
    # a question's synthetic key is flagged in the API answer
    qs = client.get("/api/v1/questions?code=QP-CI-Q8&limit=1", headers=headers).json()["items"]
    detail = client.get(f"/api/v1/questions/{qs[0]['id']}", headers=headers).json()
    assert [a["synthetic"] for a in detail["reference_answers"]] == [True]
