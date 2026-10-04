"""OCR on PostgreSQL: regions with every reading and its score, table cells, the page's OCR
stage, calibrations as operator-only content, and the worker running a booklet from upload to
``text_ready`` and on to ``segmented`` (real PDF splitter, cleaner and page transform; scripted
layout and engines; the trigram embedder)."""

from dataclasses import replace
from datetime import UTC, datetime

import psycopg
import pytest

from tarn_adapters.imaging import testing as synth
from tarn_adapters.ocr.transform import OpenCvPageTransform
from tarn_adapters.postgres.calibrations import PgCalibrationStore
from tarn_adapters.postgres.database import PostgresDatabase
from tarn_adapters.postgres.testing import Opener, TestDatabase, World
from tarn_core.domain.audit import AuditAction
from tarn_core.domain.booklet import BookletStatus, LineReading, Page, Region, RegionKind
from tarn_core.domain.common import Box, EngineRef
from tarn_core.domain.ocr import ContentClass, EngineCalibration, ReadingScore, SelectorSettings
from tarn_core.errors import EngineFailedError, NotOwnerError
from tarn_core.ids import RegionId
from tarn_core.ports.engines import DetectedRegion, TableCell
from tarn_core.ports.jobs import JOB_READ_BOOKLET, JOB_SEGMENT_BOOKLET
from tarn_core.services.ocr.reader import OrientationPolicy
from tarn_core.testing import ScriptedLayoutDetector, ScriptedOcrEngine
from tarn_worker.booklets import OcrKit

from .test_pg_pages import Env, _pdf

pytestmark = pytest.mark.integration

ENGINE = EngineRef(name="trocr", version="test")
BOX = Box(x0=10, y0=20, x1=300, y1=60)


def _page(session: Opener, world: World) -> Page:
    with session(world.a.id) as s:
        return s.booklets.pages(world.a.id, world.a.booklet.id)[0]


def _region(page: Page, region_id: RegionId, **kw: object) -> Region:
    fields: dict[str, object] = {"kind": RegionKind.TEXT_LINE, "box": BOX, **kw}
    return Region(
        id=region_id,
        college_id=page.college_id,
        page_id=page.id,
        **fields,  # type: ignore[arg-type]
    )


def test_regions_keep_readings_scores_and_table_cells(session: Opener, world: World) -> None:
    page = _page(session, world)
    calibration = EngineCalibration(engine="trocr", content_class=ContentClass.CURSIVE, version=2)
    with session(world.a.id) as s:
        table_id = RegionId(s.ids.new())
        cell_id = RegionId(s.ids.new())
        line_id = RegionId(s.ids.new())
        readings = (
            LineReading(engine=ENGINE, text="neural network", box=BOX, confidence=0.81),
            LineReading(
                engine=EngineRef(name="paddle", version="p"),
                text="neural netwerk",
                box=BOX,
                confidence=0.7,
            ),
        )
        scores = (
            ReadingScore(calibrated=0.81, agreement=0.93, lexicon=1.0, weight=1.0, score=1.525),
            ReadingScore(
                calibrated=0.7, agreement=0.93, lexicon=0.5, weight=0.4, score=0.87, competing=False
            ),
        )
        regions = [
            _region(
                page,
                line_id,
                readings=readings,
                chosen=0,
                content_class=ContentClass.CURSIVE,
                scores=scores,
                line_score=0.87,
                flagged=False,
                calibrations=(calibration.ref,),
            ),
            _region(page, table_id, kind=RegionKind.TABLE),
            _region(
                page,
                cell_id,
                readings=readings[:1],
                chosen=0,
                parent_id=table_id,
                row=1,
                col=2,
                flagged=True,
            ),
        ]
        s.booklets.replace_regions(world.a.id, page.id, regions)
    with session(world.a.id) as s:
        stored = {r.id: r for r in s.booklets.regions(world.a.id, page.id)}
    assert set(stored) == {line_id, table_id, cell_id}
    assert stored[line_id] == regions[0]
    assert stored[cell_id].parent_id == table_id and (stored[cell_id].row, stored[cell_id].col) == (
        1,
        2,
    )
    assert stored[cell_id].scores == ()
    # Replacing the page's regions drops the old ones, cells with their table.
    with session(world.a.id) as s:
        s.booklets.replace_regions(world.a.id, page.id, regions[:1])
    with session(world.a.id) as s:
        assert [r.id for r in s.booklets.regions(world.a.id, page.id)] == [line_id]


def test_the_database_keeps_scores_whole_and_cells_in_their_table(
    test_database: TestDatabase, world: World
) -> None:
    with test_database.owner() as conn:
        region = conn.execute(
            "SELECT id, college_id FROM regions WHERE college_id = %s LIMIT 1", (world.a.id,)
        ).fetchone()
    assert region is not None
    region_id, college_id = region
    insert = (
        "INSERT INTO line_readings (college_id, region_id, ordinal, engine_name, engine_version, "
        "text, box, confidence, p_calibrated, agreement, lexicon, weight, score, competing) "
        "VALUES (%s, %s, %s, 'e', '1', 't', '{0,0,1,1}', 0.5, %s, %s, %s, %s, %s, %s)"
    )
    refused: list[tuple[str, tuple[object, ...]]] = [
        # a score with missing terms
        (insert, (college_id, region_id, 99, None, None, None, None, 1.0, None)),
        # agreement above 1
        (insert, (college_id, region_id, 98, 0.5, 1.5, 0, 1, 1, True)),
        # a cell without a table
        ("UPDATE regions SET row_index = 0, col_index = 0 WHERE id = %s", (region_id,)),
    ]
    with test_database.app(college_id) as conn:  # type: ignore[arg-type]
        for statement, params in refused:
            with pytest.raises(psycopg.Error), conn.transaction():
                conn.execute(statement, params)


def test_pages_keep_the_ocr_stage(session: Opener, world: World) -> None:
    page = _page(session, world)
    with session(world.a.id) as s:
        s.booklets.save_page(
            world.a.id,
            replace(page, text_read=True, needs_text=True, ocr_failures=("trocr:timeout",)),
        )
    stored = _page(session, world)
    assert stored.text_read and stored.needs_text and stored.ocr_failures == ("trocr:timeout",)


# --- calibrations ------------------------------------------------------------------------------


def _calibration(version: int) -> EngineCalibration:
    return EngineCalibration(
        engine="trocr",
        content_class=ContentClass.CURSIVE,
        version=version,
        engine_version="microsoft/trocr-base-handwritten",
        xs=(0.2, 0.8),
        ys=(0.3, 0.9),
        weight=0.75,
        error_rate=0.12,
        samples=40,
        fitted_at=datetime(2026, 10, 4, tzinfo=UTC),
    )


def test_the_application_role_reads_but_cannot_write_calibrations(
    test_database: TestDatabase, session: Opener, world: World
) -> None:
    with pytest.raises(NotOwnerError), session(world.a.id) as s:
        s.calibrations.save(_calibration(1))
    owner = PostgresDatabase(test_database.owner_url, pool_size=1)
    try:
        with owner.engine.begin() as conn:
            store = PgCalibrationStore(conn)
            existing = store.get("trocr", ContentClass.CURSIVE)
            first = 1 if existing is None else existing.version + 1
            store.save(_calibration(first))
            store.save(_calibration(first + 1))
    finally:
        owner.dispose()
    with session(world.b.college.id) as s:  # any college reads them
        latest = s.calibrations.get("trocr", ContentClass.CURSIVE)
        assert latest == _calibration(first + 1)
        assert _calibration(first + 1) in s.calibrations.latest()
    with test_database.app(world.a.id) as conn, pytest.raises(psycopg.Error):
        conn.execute("UPDATE ocr_calibrations SET params = '{}'")


# --- the worker, end to end ---------------------------------------------------------------------


def _kit(fail_paddle: bool = False) -> OcrKit:
    lines = [
        DetectedRegion(kind=RegionKind.TEXT_LINE, box=Box(x0=40, y0=60, x1=700, y1=110)),
        DetectedRegion(
            kind=RegionKind.TABLE,
            box=Box(x0=40, y0=200, x1=700, y1=400),
            cells=(TableCell(row=0, col=0, box=Box(x0=40, y0=200, x1=360, y1=300)),),
        ),
    ]
    return OcrKit(
        layout=ScriptedLayoutDetector(lines),
        engines={
            "trocr": ScriptedOcrEngine("trocr", ["gradient descent", "cell"], 0.8),
            "paddle": ScriptedOcrEngine(
                "paddle",
                ["gradient descend", "cell"],
                0.7,
                fail=EngineFailedError("down") if fail_paddle else None,
            ),
        },
        transform=OpenCvPageTransform(),
        word_list=None,
        settings=SelectorSettings(engine_sets=dict.fromkeys(ContentClass, ("trocr", "paddle"))),
        orientation=OrientationPolicy(enabled=True, engine="paddle", min_chars=1000),
    )


@pytest.fixture
def ocr_env(fresh: tuple[TestDatabase, PostgresDatabase]) -> Env:
    env = Env(*fresh)
    env.runner.ocr = _kit(fail_paddle=True)
    return env


def test_the_worker_reads_a_booklet_after_its_pages_are_ready(ocr_env: Env) -> None:
    env = ocr_env
    page = synth.ruled_page(900, 1200, seed=3, lines=12)
    booklet = env.upload(_pdf([synth.photograph(page, size=(900, 1200), margin=0.03)] * 2))
    assert env.runner.run_one() is True  # booklet.prepare
    assert env.booklet(booklet).status is BookletStatus.PAGES_READY
    with env.test_db.owner() as conn:
        kinds = [r[0] for r in conn.execute("SELECT kind FROM jobs ORDER BY seq").fetchall()]
    assert kinds == ["booklet.prepare", JOB_READ_BOOKLET]
    assert env.runner.run_one() is True  # booklet.read
    done = env.booklet(booklet)
    assert done.status is BookletStatus.TEXT_READY
    pages = env.pages(booklet)
    assert all(p.text_read and not p.needs_text for p in pages)
    assert all(p.ocr_failures == ("paddle:error",) for p in pages)
    with env.open(env.college.id) as s:
        regions = list(s.booklets.regions(env.college.id, pages[0].id))
    assert [r.kind for r in regions] == [
        RegionKind.TEXT_LINE,
        RegionKind.TABLE,
        RegionKind.TEXT_LINE,
    ]
    assert regions[0].text == "gradient descent" and regions[0].read_by == ("trocr",)
    assert regions[2].parent_id == regions[1].id
    ((actor, after),) = env.audit(AuditAction.BOOKLET_TEXT_READ)
    assert actor is None and '"lines": 4' in str(after)
    # P12: the last reading step queued segmentation in the same transaction.
    with env.test_db.owner() as conn:
        kinds = [r[0] for r in conn.execute("SELECT kind FROM jobs ORDER BY seq").fetchall()]
    assert kinds == ["booklet.prepare", JOB_READ_BOOKLET, JOB_SEGMENT_BOOKLET]
    assert env.runner.run_one() is True  # booklet.segment
    assert env.booklet(booklet).status is BookletStatus.SEGMENTED
    with env.open(env.college.id) as s:
        segments = list(s.booklets.segments(env.college.id, booklet.id))
    assert segments and {rid for seg in segments for rid in seg.region_ids} >= {
        r.id for r in regions
    }
    assert [p.reading_order for p in env.pages(booklet)] == [0, 1]
    assert env.runner.run_one() is False
