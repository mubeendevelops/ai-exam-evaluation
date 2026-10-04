"""OCR calibrations on PostgreSQL (``ocr_calibrations``, migration 0006): global, numbers only,
readable by every college; only the migration owner (the Tarn operator's ``tarn ocr
calibrate``) may insert, and a version is never overwritten."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Row, insert, select
from sqlalchemy.dialects.postgresql import distinct_on

from tarn_adapters.postgres import metadata as m
from tarn_adapters.postgres.repositories import writing
from tarn_core.domain.ocr import ContentClass, EngineCalibration
from tarn_core.errors import NotOwnerError

_T = m.ocr_calibrations


def _params(c: EngineCalibration) -> dict[str, object]:
    return {
        "xs": list(c.xs),
        "ys": list(c.ys),
        "weight": c.weight,
        "error_rate": c.error_rate,
        "samples": c.samples,
        "fitted_at": None if c.fitted_at is None else c.fitted_at.isoformat(),
    }


def _calibration(r: Row[Any]) -> EngineCalibration:
    p = r.params
    return EngineCalibration(
        engine=r.engine_name,
        content_class=ContentClass(r.content_class),
        version=r.version,
        engine_version=r.engine_version,
        xs=tuple(float(x) for x in p["xs"]),
        ys=tuple(float(y) for y in p["ys"]),
        weight=float(p["weight"]),
        error_rate=None if p.get("error_rate") is None else float(p["error_rate"]),
        samples=int(p.get("samples", 0)),
        fitted_at=None if p.get("fitted_at") is None else datetime.fromisoformat(p["fitted_at"]),
    )


class PgCalibrationStore:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def latest(self) -> Sequence[EngineCalibration]:
        stmt = (
            select(_T)
            .ext(distinct_on(_T.c.engine_name, _T.c.content_class))
            .order_by(_T.c.engine_name, _T.c.content_class, _T.c.version.desc())
        )
        return [_calibration(r) for r in self._conn.execute(stmt)]

    def get(self, engine: str, content_class: ContentClass) -> EngineCalibration | None:
        row = self._conn.execute(
            select(_T)
            .where(_T.c.engine_name == engine, _T.c.content_class == content_class.value)
            .order_by(_T.c.version.desc())
            .limit(1)
        ).first()
        return None if row is None else _calibration(row)

    def save(self, calibration: EngineCalibration) -> None:
        with writing(self._conn, f"calibration {calibration.engine}", NotOwnerError):
            self._conn.execute(
                insert(_T).values(
                    id=calibration.id,
                    version=calibration.version,
                    engine_name=calibration.engine,
                    engine_version=calibration.engine_version or "unknown",
                    content_class=calibration.content_class.value,
                    params=_params(calibration),
                    owning_college_id=None,
                    created_by=None,
                )
            )
