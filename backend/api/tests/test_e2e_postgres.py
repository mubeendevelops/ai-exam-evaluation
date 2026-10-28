"""P21 and P30 on the real stack (``make up``; ``make test-integration``): the teacher's journey
with five queued booklets, the admin portal's journey from a new college to a frozen unit, the
cross-college sweep and the persona sweep of every endpoint, on PostgreSQL as ``tarn_app`` and
``tarn_auth`` (row-level security), MinIO, and the worker's runner on the real job queue. Then
what the application role can and cannot do in SQL to the audit log, and O7 as accepted risk."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient

from tarn_adapters.config import Settings
from tarn_adapters.identity.testing import create_test_identity_database
from tarn_adapters.postgres.testing import TestDatabase, create_test_database, drop_test_database
from tarn_adapters.runtime import SystemClock, UuidGenerator
from tarn_adapters.seed.runner import run_seed
from tarn_adapters.testing import QP_CI_PAGE, scripted_stages
from tarn_api import persona_sweep
from tarn_api.admin_journey import JOURNEY_PAGE, AdminJourney
from tarn_api.app import create_app
from tarn_api.backends import PostgresBackends, unit_of_work
from tarn_api.journey import Event, Journey, event_payload
from tarn_api.persona_sweep import ACCESS, PERSONAS, checked
from tarn_api.security import ROLE_REFUSED
from tarn_api.tenancy_sweep import (
    A_INSTITUTION,
    B_INSTITUTION,
    CASES,
    World,
    build_world,
    call,
    login,
    upload_for_another_college,
)
from tarn_core.domain.common import BlobKey
from tarn_core.domain.personas import CAPABILITIES
from tarn_core.domain.tenancy import Role
from tarn_core.ids import CollegeId
from tarn_core.seed import DEV_PASSWORD
from tarn_core.services.pipeline import QualityPolicy
from tarn_core.testing import FakePageCleaner, FakePageSplitter, MemoryMailer
from tarn_worker.booklets import BookletJobRunner

pytestmark = pytest.mark.integration

EXPECTED = {"college": {404}}


@dataclass
class Stack:
    client: TestClient
    app_db: TestDatabase
    backends: PostgresBackends
    runner: BookletJobRunner
    mail: MemoryMailer

    def process(self) -> None:
        for _ in range(200):
            if not self.runner.run_one():
                return
        raise AssertionError("the queue did not drain")

    def events(self, college: UUID) -> list[Event]:
        with self.app_db.owner() as conn:
            rows = conn.execute(
                "SELECT action, booklet_id, before, after FROM audit_events "
                "WHERE college_id = %s ORDER BY seq",
                (college,),
            ).fetchall()
        return [
            Event(str(action), booklet if isinstance(booklet, UUID) else None, event_payload(b, a))
            for action, booklet, b, a in rows
        ]

    def governor(self, institution: str, email: str) -> dict[str, str]:
        """The operator creates the college's Governor (``tarn tenants governor``), who
        accepts the invitation and signs in."""
        with self.backends.identity() as store:
            tenant = store.find_tenant(institution)
        assert tenant is not None
        with unit_of_work(self.backends, tenant.college_id) as unit:
            unit.accounts.invite_governor(
                tenant.college_id, display_name="Governor", email=email, operator="test"
            )
        accepted = self.client.post(
            "/api/v1/auth/invitations/accept",
            json={"token": self.mail.last_token(email), "password": DEV_PASSWORD},
        )
        assert accepted.status_code == 204, accepted.text
        return login(self.client, institution, email)


@contextmanager
def open_stack(
    keys: Path, *, seed: bool = True, lines: Sequence[str] = QP_CI_PAGE
) -> Iterator[Stack]:
    """Throwaway application and identity databases, MinIO, the worker's runner with scripted
    OCR reading ``lines``; the demo seed unless ``seed`` is false."""
    base = Settings()
    app_db = create_test_database(base.database_url, base.app_database_url)
    identity_db = create_test_identity_database(base.database_url, base.identity_app_database_url)
    settings = Settings(
        app_database_url=app_db.app_url,
        identity_app_database_url=identity_db.app_url,
        local_key_dir=keys,
        device="cpu",
        rate_limits_enabled=False,
    )
    backends = PostgresBackends(settings)
    mail = MemoryMailer()
    backends.kit = replace(backends.kit, mailer=mail)
    if seed:
        run_seed(settings, blobs=backends._blobs)
    ocr = scripted_stages(backends._blobs, lines).ocr
    runner = BookletJobRunner(
        db=backends._app,
        blobs=backends._blobs,
        splitter=FakePageSplitter(),
        cleaner=FakePageCleaner(),
        clock=SystemClock(),
        ids=UuidGenerator(),
        policy=QualityPolicy(),
        max_pages=40,
        worker="p21-e2e",
        ocr=ocr,
    )
    try:
        with TestClient(create_app(settings, backends=backends)) as client:
            yield Stack(client, app_db, backends, runner, mail)
    finally:
        backends.dispose()
        drop_test_database(base.database_url, app_db.name)
        drop_test_database(base.database_url, identity_db.name)


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    with open_stack(tmp_path_factory.mktemp("keys")) as opened:
        yield opened


# --- the journey -------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def journey(stack: Stack) -> dict[str, Any]:
    lines = [line.lstrip("^> ") for line in QP_CI_PAGE]
    return Journey(stack.client, stack.process, stack.events, student_texts=lines).run()


def test_teacher_journey_with_five_queued_booklets_on_postgres(
    stack: Stack, journey: dict[str, Any]
) -> None:
    gone = UUID(journey["deleted"])
    college = journey["college"]
    with stack.app_db.owner() as conn:
        # Deletion: no row of the booklet is left anywhere; one content-free record stays.
        assert conn.execute("SELECT count(*) FROM booklets WHERE id = %s", (gone,)).fetchone() == (
            0,
        )
        record = conn.execute(
            "SELECT college_id, booklet_id FROM deletion_records WHERE booklet_id = %s", (gone,)
        ).fetchall()
        assert record == [(college, gone)]
        # Earlier events of the deleted booklet were redacted in place (D32): who did what and
        # when stays, every before/after is gone.
        kept = conn.execute(
            "SELECT action, before, after FROM audit_events WHERE booklet_id = %s ORDER BY seq",
            (gone,),
        ).fetchall()
        assert kept[-1][0] == "booklet.deleted" and len(kept) > 5
        assert all(row[1:] == (None, None) for row in kept[:-1]), kept
    prefix = BlobKey(f"college/{college}/booklet/{gone}")
    assert stack.backends._blobs.delete_prefix(prefix) == 0  # nothing was left to delete


def test_the_audit_log_cannot_be_changed_by_the_application_role(
    stack: Stack, journey: dict[str, Any]
) -> None:
    college = journey["college"]
    with stack.app_db.app(college) as conn:
        assert conn.execute("SELECT count(*) FROM audit_events").fetchone() != (0,)
    for statement in (
        "UPDATE audit_events SET action = 'tampered'",
        "DELETE FROM audit_events",
        "TRUNCATE audit_events",
        "UPDATE deletion_records SET booklet_id = gen_random_uuid()",
        "DELETE FROM deletion_records",
    ):
        with pytest.raises(psycopg.Error), stack.app_db.app(college) as conn:
            conn.execute(statement)


def test_o7_the_application_role_can_still_name_another_college(stack: Stack) -> None:
    """Accepted risk (D147): ``tarn_app`` may set ``app.college_id`` itself, so a statement it
    runs can name any college. RLS stops crafted *queries*, not injected *statements*; hence no
    SQL is ever built from strings (``adapters/tests/test_sql_hygiene.py``)."""
    with stack.app_db.owner() as conn:
        colleges = [row[0] for row in conn.execute("SELECT id FROM colleges ORDER BY name")]
    a, b = CollegeId(UUID(str(colleges[0]))), CollegeId(UUID(str(colleges[1])))
    with stack.app_db.app(a) as conn:
        assert conn.execute("SELECT count(*) FROM colleges").fetchone() == (1,)
        conn.execute("SELECT set_config('app.college_id', %s, true)", (str(b),))
        assert conn.execute("SELECT id FROM colleges").fetchall() == [(b,)]


# --- the sweep ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def world(stack: Stack, journey: dict[str, Any]) -> World:
    built = build_world(stack.client, stack.process)
    built.b_governor = stack.governor(B_INSTITUTION, "governor@demo-eng.example.test")
    return built


@pytest.mark.parametrize(
    ("method", "path"),
    [op for op, case in CASES.items() if case.kind != "own"],
    ids=lambda v: str(v),
)
def test_college_b_cannot_reach_college_a_on_postgres(
    stack: Stack, world: World, method: str, path: str
) -> None:
    case = CASES[(method, path)]
    a_college = UUID(world.ids["a_college"])
    before = stack.events(a_college)
    review = stack.client.get(
        f"/api/v1/booklets/{world.ids['booklet_id']}/review", headers=world.a
    ).json()
    response = call(world, method, path, case)
    assert response.status_code in EXPECTED[case.kind], (method, path, response.text)
    assert stack.events(a_college) == before
    after = stack.client.get(
        f"/api/v1/booklets/{world.ids['booklet_id']}/review", headers=world.a
    ).json()
    assert after == review


def test_b_cannot_register_a_booklet_for_a_student_of_a_on_postgres(world: World) -> None:
    assert upload_for_another_college(world).status_code == 404


# --- the admin portal's journey (P30) ----------------------------------------------------------


def test_admin_portal_journey_on_postgres(tmp_path_factory: pytest.TempPathFactory) -> None:
    """A new college on empty databases (no seed): see ``tarn_api.admin_journey``."""
    with open_stack(tmp_path_factory.mktemp("keys"), seed=False, lines=JOURNEY_PAGE) as s:
        journey = AdminJourney(s.client, s.backends, s.process, s.events, s.mail)
        result = journey.run()
        college, unit = result["college"], result["unit"]
        with s.app_db.owner() as conn:
            # The paper's rows are gone; the frozen record and every sheet row stay.
            assert conn.execute(
                "SELECT count(*) FROM exam_blueprints WHERE id = %s", (UUID(result["paper"]),)
            ).fetchone() == (0,)
            assert conn.execute(
                "SELECT state FROM units WHERE id = %s", (UUID(unit),)
            ).fetchone() == ("frozen",)
            assert conn.execute(
                "SELECT count(*) FROM frozen_booklets WHERE unit_id = %s", (UUID(unit),)
            ).fetchone() == (5,)
            assert conn.execute(
                "SELECT count(*) FROM result_sheets WHERE college_id = %s", (college,)
            ).fetchone() == (6,)
        sheets = BlobKey(f"college/{college}/booklet/{result['amended']}/sheets/v2.pdf")
        assert s.backends._blobs.get(sheets).startswith(b"%PDF")


# --- the persona sweep on PostgreSQL (P30) -----------------------------------------------------


@pytest.fixture(scope="module")
def personas(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[World, dict[str, dict[str, str]]]]:
    """A stack of its own: the sweep submits, freezes and deletes."""
    with open_stack(tmp_path_factory.mktemp("keys")) as s:
        governor = s.governor(A_INSTITUTION, "governor@demo-com.example.test")
        w = build_world(s.client, s.process)
        yield w, {"admin": w.a_admin, "sme": w.a, "governor": governor}


@pytest.mark.parametrize(("method", "path"), checked(), ids=lambda v: str(v))
def test_each_persona_is_allowed_or_refused_on_postgres(
    personas: tuple[World, dict[str, dict[str, str]]], method: str, path: str
) -> None:
    world, headers = personas
    access = ACCESS[(method, path)]
    assert isinstance(access, frozenset)
    for persona in PERSONAS:
        allowed = bool(access & CAPABILITIES[Role(persona)])
        response = persona_sweep.call(world, headers[persona], method, path)
        if allowed:
            assert response.status_code not in (401, 403), (persona, method, path, response.text)
        else:
            assert response.status_code == 403, (persona, method, path, response.text)
            assert response.json() == {"detail": ROLE_REFUSED}
