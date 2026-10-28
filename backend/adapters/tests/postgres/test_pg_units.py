"""Units of work on PostgreSQL (P26): migration 0014's back-fill of earlier booklets, the
refusal without an entitled SME, row-level security on ``units`` and the repository's
uniqueness and stale-write rules."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import psycopg
import pytest

from tarn_adapters.config import Settings
from tarn_adapters.postgres import migrate
from tarn_adapters.postgres.testing import (
    Opener,
    TestDatabase,
    create_test_database,
    drop_test_database,
    new_college,
)
from tarn_core.domain.unit import Unit
from tarn_core.errors import AlreadyExistsError, NotFoundError, StaleWriteError
from tarn_core.ids import CollegeId, UnitId
from tarn_core.testing.builders import ci_shaped_blueprint, unit_for

pytestmark = pytest.mark.integration

Conn = psycopg.Connection[tuple[object, ...]]
UPLOADED = datetime(2025, 3, 1, 9, 0, tzinfo=UTC)


@pytest.fixture
def at_0013() -> Iterator[TestDatabase]:
    settings = Settings()
    db = create_test_database(settings.database_url, settings.app_database_url)
    try:
        migrate.downgrade(db.owner_url, "0013")
        yield db
    finally:
        drop_test_database(settings.database_url, db.name)


def _college_with_a_booklet(conn: Conn, code: str, *, entitled: bool) -> dict[str, UUID]:
    """A college of 0013: admin, SME, subject (granted to the SME or not), paper, student and
    one booklet on the paper."""
    ids = {k: uuid4() for k in ("college", "admin", "sme", "subject", "paper", "student", "b")}
    conn.execute(
        "INSERT INTO colleges (id, name, code) VALUES (%s, %s, %s)",
        (ids["college"], f"Test {code}", code),
    )
    for who, role in (("admin", "admin"), ("sme", "sme")):
        conn.execute(
            "INSERT INTO users (id, college_id, display_name, email, role) "
            "VALUES (%s, %s, %s, %s, %s)",
            (ids[who], ids["college"], f"{who} {code}", f"{who}@{code.lower()}.example", role),
        )
    conn.execute(
        "INSERT INTO subjects (id, version, code, name, college_id, created_by) "
        "VALUES (%s, 1, 'S', 'Synthetic', %s, %s)",
        (ids["subject"], ids["college"], ids["sme"]),
    )
    if entitled:
        conn.execute(
            "INSERT INTO sme_subjects (college_id, user_id, subject_id, granted_at) "
            "VALUES (%s, %s, %s, now())",
            (ids["college"], ids["sme"], ids["subject"]),
        )
    conn.execute(
        "INSERT INTO exam_blueprints (id, version, subject_id, title, total_marks, mark_step, "
        "sections, college_id, created_by) VALUES (%s, 1, %s, 'Synthetic paper', 2, 0.5, "
        "'[]', %s, %s)",
        (ids["paper"], ids["subject"], ids["college"], ids["sme"]),
    )
    conn.execute(
        "INSERT INTO students (id, college_id, name, usn) VALUES (%s, %s, 'Student', %s)",
        (ids["student"], ids["college"], f"TST{code}0001"),
    )
    conn.execute(
        "INSERT INTO booklets (id, college_id, student_id, blueprint_id, blueprint_version, "
        "file_sha256, uploaded_by, uploaded_at, status, version, sources) "
        "VALUES (%s, %s, %s, %s, 1, %s, %s, %s, 'uploaded', 1, '[]')",
        (
            ids["b"],
            ids["college"],
            ids["student"],
            ids["paper"],
            "a" * 64,
            ids["admin"],
            UPLOADED,
        ),
    )
    return ids


def test_0014_puts_earlier_booklets_into_a_unit(at_0013: TestDatabase) -> None:
    db = at_0013
    with db.owner() as conn:
        mine = _college_with_a_booklet(conn, "UA", entitled=True)
        theirs = _college_with_a_booklet(conn, "UB", entitled=True)
    migrate.upgrade(db.owner_url)
    assert migrate.current_revision(db.owner_url) == "0015"
    with db.owner() as conn:
        rows = conn.execute(
            "SELECT college_id, code, paper_id, subject_id, year, owner_id, created_by FROM units"
        ).fetchall()
        assert {r[0] for r in rows} == {mine["college"], theirs["college"]}
        (row,) = [r for r in rows if r[0] == mine["college"]]
        assert row[1:] == ("U-0001", mine["paper"], mine["subject"], "2025", mine["sme"], None)
        (unit_of_booklet,) = conn.execute(
            "SELECT unit_id FROM booklets WHERE id = %s", (mine["b"],)
        ).fetchone() or (None,)
        assert unit_of_booklet is not None
    with db.app(CollegeId(mine["college"])) as conn:  # RLS: one college's units only
        assert len(conn.execute("SELECT * FROM units").fetchall()) == 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM units")


def test_0014_refuses_booklets_without_an_entitled_sme(at_0013: TestDatabase) -> None:
    db = at_0013
    with db.owner() as conn:
        _college_with_a_booklet(conn, "UC", entitled=False)
    with pytest.raises(Exception, match="no active SME entitled"):
        migrate.upgrade(db.owner_url)
    assert migrate.current_revision(db.owner_url) == "0013"


def test_units_are_private_unique_and_versioned(session: Opener) -> None:
    a = new_college(session, "UNA")
    b = new_college(session, "UNB")
    with session(a.id) as s:
        paper = ci_shaped_blueprint(s, a)
        unit_id = unit_for(s, a.id, paper.id)
        unit = s.units.get(a.id, unit_id)
        assert s.units.find(a.id, paper.id, "2024") == unit
        assert [u.id for u in s.units.list(a.id, owner_id=a.teacher.id)] == [unit_id]
        assert s.units.count(a.id) == 1
    with session(b.id) as s:
        with pytest.raises(NotFoundError):
            s.units.get(b.id, unit_id)
        assert s.units.list(b.id) == []
    with session(a.id) as s:
        clash = Unit(
            id=UnitId(uuid4()),
            college_id=a.id,
            code="U-0002",
            paper_id=paper.id,
            subject_id=paper.subject_id,
            year="2024",
            owner_id=a.teacher.id,
            due_date=date(2030, 1, 1),
            created_by=a.admin.id,
            created_at=UPLOADED,
        )
        with pytest.raises(AlreadyExistsError):  # same paper and year
            s.units.save(a.id, replace(clash, year="2024"))
        with pytest.raises(StaleWriteError):  # the same version again
            s.units.save(a.id, replace(unit, due_date=date(2030, 2, 1)))
        s.units.save(a.id, replace(unit, due_date=date(2030, 2, 1), version=2))
        assert s.units.get(a.id, unit_id).due_date == date(2030, 2, 1)
