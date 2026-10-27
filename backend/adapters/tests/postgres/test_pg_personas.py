"""P24 on PostgreSQL: migration 0013 (teachers become SMEs with every subject of their college,
two admins refused), one admin and one governor per college, subject grants under RLS, the
per-address lockout counters of the shared admin login (i0003), and ``tarn tenants governor``."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from typer.testing import CliRunner

from tarn_adapters.config import Settings, get_settings
from tarn_adapters.identity.database import IdentityDatabase
from tarn_adapters.identity.testing import create_test_identity_database
from tarn_adapters.postgres import migrate
from tarn_adapters.postgres.testing import (
    Opener,
    TestDatabase,
    World,
    create_test_database,
    drop_test_database,
)
from tarn_adapters.seed import run_seed
from tarn_cli.main import app as cli
from tarn_core.domain.tenancy import Role, SubjectGrant, User
from tarn_core.errors import InvariantError, TenantViolationError
from tarn_core.ids import CollegeId, UserId
from tarn_core.seed import COMMERCE
from tarn_core.testing import InMemory

pytestmark = pytest.mark.integration

Conn = psycopg.Connection[tuple[object, ...]]


@pytest.fixture
def at_0012() -> Iterator[TestDatabase]:
    settings = Settings()
    db = create_test_database(settings.database_url, settings.app_database_url)
    try:
        migrate.downgrade(db.owner_url, "0012")
        yield db
    finally:
        drop_test_database(settings.database_url, db.name)


def _college(conn: Conn, code: str, *roles: str) -> tuple[UUID, list[UUID]]:
    college = uuid4()
    conn.execute(
        "INSERT INTO colleges (id, name, code) VALUES (%s, %s, %s)", (college, f"Test {code}", code)
    )
    people = []
    for n, role in enumerate(roles):
        person = uuid4()
        conn.execute(
            "INSERT INTO users (id, college_id, display_name, email, role) "
            "VALUES (%s, %s, 'Person', %s, %s)",
            (person, college, f"p{n}@{code.lower()}.example", role),
        )
        people.append(person)
    return college, people


def test_0013_turns_teachers_into_smes_with_every_subject(at_0012: TestDatabase) -> None:
    db = at_0012
    with db.owner() as conn:
        college, (admin, teacher) = _college(conn, "PA", "admin", "teacher")
        other, (_, other_teacher) = _college(conn, "PB", "admin", "teacher")
        subjects = [uuid4(), uuid4()]
        for n, subject in enumerate(subjects):
            conn.execute(
                "INSERT INTO subjects (id, version, code, name, college_id, created_by) "
                "VALUES (%s, 1, %s, 'Synthetic', %s, %s)",
                (subject, f"S{n}", college, teacher),
            )
    migrate.upgrade(db.owner_url)
    assert migrate.current_revision(db.owner_url) == "0013"
    with db.owner() as conn:
        roles = {r[0]: r[1] for r in conn.execute("SELECT id, role FROM users").fetchall()}
        assert roles[admin] == "admin" and roles[teacher] == roles[other_teacher] == "sme"
        grants = conn.execute(
            "SELECT college_id, user_id, subject_id, granted_by FROM sme_subjects"
        ).fetchall()
        assert {(c, u, s, g) for c, u, s, g in grants} == {
            (college, teacher, s, None) for s in subjects
        }  # every subject of its own college; none of another college's
        assert other not in {c for c, *_ in grants}
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE users SET role = 'teacher' WHERE id = %s", (teacher,))
    with db.app(CollegeId(college)) as conn:
        assert len(conn.execute("SELECT * FROM sme_subjects").fetchall()) == 2
    with db.app(CollegeId(other)) as conn:
        assert conn.execute("SELECT * FROM sme_subjects").fetchall() == []  # RLS


def test_0013_refuses_a_college_with_two_admins(at_0012: TestDatabase) -> None:
    db = at_0012
    with db.owner() as conn:
        _college(conn, "TA", "admin", "admin")
    with pytest.raises(Exception, match="more than one admin"):
        migrate.upgrade(db.owner_url)
    assert migrate.current_revision(db.owner_url) == "0012"


def test_one_admin_and_one_governor_per_college(session: Opener, world: World) -> None:
    a = world.a.college
    with session(a.id) as s, pytest.raises(InvariantError):
        s.users.save(
            a.id,
            User(
                id=UserId(uuid4()),
                college_id=a.id,
                display_name="Second admin",
                email="second.admin@example.test",
                role=Role.ADMIN,
            ),
        )
    governor = User(
        id=UserId(uuid4()),
        college_id=a.id,
        display_name="Governor",
        email="governor@example.test",
        role=Role.GOVERNOR,
    )
    with session(a.id) as s:
        s.users.save(a.id, governor)
    with session(a.id) as s, pytest.raises(InvariantError):
        s.users.save(a.id, replace(governor, id=UserId(uuid4()), email="g2@example.test"))


def test_subject_grants_stay_in_the_college(session: Opener, world: World) -> None:
    a, b = world.a.college, world.b.college
    with session(a.id) as s:
        held = s.entitlements.subjects_of(a.id, a.teacher.id)
        assert held  # the builders grant the SME their paper's subject
        subject = next(iter(held))
        s.entitlements.revoke(a.id, a.teacher.id, subject)
        assert subject not in s.entitlements.subjects_of(a.id, a.teacher.id)
        s.entitlements.grant(
            a.id,
            SubjectGrant(
                college_id=a.id,
                user_id=a.teacher.id,
                subject_id=subject,
                granted_by=a.admin.id,
                granted_at=datetime.now(UTC),
            ),
        )
        assert [g.granted_by for g in s.entitlements.grants(a.id)][-1] == a.admin.id
    with session(b.id) as s:
        assert subject not in s.entitlements.subjects_of(b.id, a.teacher.id)
        # B cannot grant A's subject to its own SME: the composite key finds no such subject.
        with pytest.raises((TenantViolationError, InvariantError, LookupError)):
            s.entitlements.grant(
                b.id,
                SubjectGrant(
                    college_id=b.id,
                    user_id=b.teacher.id,
                    subject_id=subject,
                    granted_by=None,
                    granted_at=datetime.now(UTC),
                ),
            )


# --- tarn tenants governor -----------------------------------------------------------------------


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """The demo seed in databases of its own (the shared test databases hold other tests'
    colleges; the seed test counts what it creates)."""
    base = Settings()
    app_db = create_test_database(base.database_url, base.app_database_url)
    identity_db = create_test_identity_database(base.database_url, base.identity_app_database_url)
    try:
        monkeypatch.setenv("TARN_APP_DATABASE_URL", app_db.app_url)
        monkeypatch.setenv("TARN_IDENTITY_APP_DATABASE_URL", identity_db.app_url)
        monkeypatch.setenv("TARN_LOCAL_KEY_DIR", str(tmp_path))
        get_settings.cache_clear()
        settings = get_settings()
        run_seed(settings, blobs=InMemory().blobs)
        yield settings
    finally:
        get_settings.cache_clear()
        drop_test_database(base.database_url, app_db.name)
        drop_test_database(base.database_url, identity_db.name)


def test_the_operator_creates_one_governor_per_college(seeded: Settings) -> None:
    runner = CliRunner()
    email = "governor@demo-com.example.test"
    args = ["tenants", "governor", COMMERCE.institution_id, "--email", email]
    refused = runner.invoke(cli, args)
    assert refused.exit_code != 0  # --operator is required
    done = runner.invoke(cli, [*args, "--operator", "ops@tarn"])
    assert done.exit_code == 0, done.output
    assert "invited" in done.output
    again = runner.invoke(cli, [*args[:-1], "second@demo-com.example.test", "--operator", "ops"])
    assert again.exit_code == 1 and "already has a Governor" in again.output
    unknown = runner.invoke(
        cli, ["tenants", "governor", "NOPE", "--email", email, "--operator", "ops"]
    )
    assert unknown.exit_code == 1 and "no tenant" in unknown.output
    identity = IdentityDatabase(seeded.identity_app_database_url)
    try:
        with identity.session() as store:
            tenant = store.find_tenant(COMMERCE.institution_id)
            assert tenant is not None
            found = store.find_identity(tenant.college_id, email)
            assert found is not None and found.status.value == "PENDING"
    finally:
        identity.dispose()
