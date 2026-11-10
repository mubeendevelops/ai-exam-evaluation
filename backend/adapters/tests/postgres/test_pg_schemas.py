"""Schemas and papers on PostgreSQL (P33, D219, D231): the list contract as ``tarn_app`` under
row-level security, the 1.2 columns round-tripping, the CHECK that keeps a schema a template,
and migration 0019's back-fill on a seeded database."""

import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy.exc import IntegrityError

from tarn_adapters.config import Settings
from tarn_adapters.identity.testing import create_test_identity_database
from tarn_adapters.postgres import codec, migrate
from tarn_adapters.postgres.testing import (
    Opener,
    create_test_database,
    drop_test_database,
    new_college,
)
from tarn_adapters.seed import run_seed
from tarn_core.domain.blueprint import BlueprintKind, ExamBlueprint, QuestionSlot, Section
from tarn_core.testing import InMemory
from tarn_core.testing.blueprint_list_contract import build, check, check_isolated, schema_document
from tarn_core.testing.builders import make_services

pytestmark = pytest.mark.integration


def test_the_blueprint_lists_on_postgres_match_the_contract(session: Opener) -> None:
    a = new_college(session, "BLA")
    b = new_college(session, "BLB")
    with session(a.id) as s:
        scenario = build(s, a)
        check(s.content, scenario)
    with session(b.id) as s:
        # B's session asks for A's college: row-level security leaves nothing.
        check_isolated(s.content, a.id)
        check_isolated(s.content, b.id)


def test_the_new_columns_round_trip(session: Opener) -> None:
    a = new_college(session, "BLC")
    with session(a.id) as s:
        scenario = build(s, a)
        svc = make_services(s).blueprints
        paper = svc.generate_paper(a.id, a.teacher.id, scenario.matrix.id, title="Round trip")
    with session(a.id) as s:
        stored = s.content.get(a.id, ExamBlueprint, paper.id)
        assert stored == paper and stored.saved_at == session.clock.now()
        assert stored.kind is BlueprintKind.PAPER and stored.from_schema == scenario.matrix.id
        assert stored.target_batch == "Class 12"
        schema = s.content.get(a.id, ExamBlueprint, scenario.matrix.id)
        assert schema.kind is BlueprintKind.SCHEMA


def test_a_linked_schema_is_refused_by_the_database(session: Opener) -> None:
    """The CHECK of 0019 holds whatever the service does: a crafted schema row with a link,
    an exam code or a ``from_schema`` is refused."""
    a = new_college(session, "BLD")
    with session(a.id) as s:
        scenario = build(s, a)
    linked = codec.sections_to_json(scenario.paper.sections)
    plain = codec.sections_to_json(
        (Section(label="A", items=(QuestionSlot(label="1", marks=Decimal(2)),)),)
    )
    bad = (
        ("sections", linked, "", None),
        ("exam code", plain, "X-1", None),
        ("from schema", plain, "", scenario.matrix.id),
    )
    for what, sections, exam_code, from_schema in bad:
        with session(a.id) as s, pytest.raises(IntegrityError, match="schema_shape"):
            s.conn.exec_driver_sql(
                "INSERT INTO exam_blueprints (id, version, subject_id, title, total_marks, "
                "mark_step, sections, kind, exam_code, from_schema, created_by, college_id) "
                "VALUES (%s, 1, %s, %s, 2, 0.5, %s::jsonb, 'schema', %s, %s, %s, %s)",
                (
                    uuid4(),
                    scenario.subject,
                    what,
                    json.dumps(sections),
                    exam_code,
                    from_schema,
                    a.teacher.id,
                    a.id,
                ),
            )


def test_0019_back_fills_kinds_on_a_seeded_database(tmp_path: Path) -> None:
    """Seed papers stay papers; a code-less, unlinked blueprint becomes a schema; a blueprint
    that had an exam code in any version is a paper in every version."""
    settings = Settings()
    app_db = create_test_database(settings.database_url, settings.app_database_url)
    identity_db = create_test_identity_database(
        settings.database_url, settings.identity_app_database_url
    )
    try:
        seeded = Settings(
            app_database_url=app_db.app_url,
            identity_app_database_url=identity_db.app_url,
            local_key_dir=tmp_path,
            device="cpu",
        )
        run_seed(seeded, blobs=InMemory().blobs)
        migrate.downgrade(app_db.owner_url, "0018")
        plain = json.dumps(
            codec.sections_to_json(
                (Section(label="A", items=(QuestionSlot(label="1", marks=Decimal(2)),)),)
            )
        )
        template, draft = uuid4(), uuid4()
        with app_db.owner() as conn:
            college_id, subject_id, created_by = _one(
                conn, "SELECT college_id, subject_id, created_by FROM exam_blueprints LIMIT 1"
            )
            seed_ids = {r[0] for r in conn.execute("SELECT DISTINCT id FROM exam_blueprints")}
            assert len(seed_ids) == 4
            for blueprint, version, code in ((template, 1, ""), (draft, 1, ""), (draft, 2, "BF-1")):
                conn.execute(
                    "INSERT INTO exam_blueprints (id, version, subject_id, title, total_marks, "
                    "mark_step, sections, exam_code, created_by, college_id) "
                    "VALUES (%s, %s, %s, 'Back-fill', 2, 0.5, %s::jsonb, %s, %s, %s)",
                    (blueprint, version, subject_id, plain, code, created_by, college_id),
                )
        migrate.upgrade(app_db.owner_url)
        assert migrate.current_revision(app_db.owner_url) == "0020"
        with app_db.owner() as conn:
            kinds = {
                (r[0], r[1]): r[2]
                for r in conn.execute("SELECT id, version, kind FROM exam_blueprints")
            }
            forced = {
                r[0]: r[1]
                for r in conn.execute(
                    "SELECT relname, relforcerowsecurity FROM pg_class "
                    "WHERE relname IN ('exam_blueprints', 'units', 'booklets')"
                )
            }
            # The CHECK holds the seed papers: none can be made a schema.
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "UPDATE exam_blueprints SET kind = 'schema' WHERE id = %s",
                    (next(iter(seed_ids)),),
                )
        assert {kinds[k] for k in kinds if k[0] in seed_ids} == {"paper"}
        assert kinds[(template, 1)] == "schema"
        assert kinds[(draft, 1)] == kinds[(draft, 2)] == "paper"
        assert forced == {"exam_blueprints": True, "units": True, "booklets": True}
        # And the downgrade drops the columns again.
        migrate.downgrade(app_db.owner_url, "0018")
        with app_db.owner() as conn:
            columns = {
                r[0]
                for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'exam_blueprints'"
                )
            }
        assert "kind" not in columns and "from_schema" not in columns
    finally:
        drop_test_database(settings.database_url, app_db.name)
        drop_test_database(settings.database_url, identity_db.name)


def _one(conn: psycopg.Connection[tuple[object, ...]], query: str) -> tuple[object, ...]:
    row = conn.execute(query).fetchone()
    assert row is not None
    return row


def test_schema_documents_are_valid_for_the_service(session: Opener) -> None:
    """The contract's document is what the API takes (a sanity check of the helper)."""
    a = new_college(session, "BLE")
    with session(a.id) as s:
        paper = build(s, a).paper
        made = make_services(s).blueprints.create(
            a.id, a.teacher.id, schema_document(paper.subject_id, "Helper")
        )
        assert made.kind is BlueprintKind.SCHEMA
