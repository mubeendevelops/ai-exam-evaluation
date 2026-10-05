"""Scoring calibrations on PostgreSQL (``scoring_calibrations``, migration 0008): global,
numbers only, readable by every college; only the migration owner (the Tarn operator's
``tarn score calibrate``) may insert, and a version is never changed."""

from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Row, func, insert, select

from tarn_adapters.postgres import metadata as m
from tarn_adapters.postgres.repositories import writing
from tarn_core.domain.scoring import ScoringCalibration
from tarn_core.errors import InvariantError, NotOwnerError

_T = m.scoring_calibrations


def _params(c: ScoringCalibration) -> dict[str, object]:
    return {
        "half": c.half,
        "full": c.full,
        "margin": c.margin,
        "relevance_min": c.relevance_min,
        "relevance_soft": c.relevance_soft,
        "samples": c.samples,
        "mean_abs_diff": c.mean_abs_diff,
        "fitted_at": None if c.fitted_at is None else c.fitted_at.isoformat(),
    }


def _calibration(r: Row[Any]) -> ScoringCalibration:
    p = r.params
    return ScoringCalibration(
        embedder=r.embedder_name,
        version=r.version,
        half=float(p["half"]),
        full=float(p["full"]),
        margin=float(p["margin"]),
        relevance_min=float(p["relevance_min"]),
        relevance_soft=float(p["relevance_soft"]),
        samples=int(p.get("samples", 0)),
        mean_abs_diff=None if p.get("mean_abs_diff") is None else float(p["mean_abs_diff"]),
        fitted_at=None if p.get("fitted_at") is None else datetime.fromisoformat(p["fitted_at"]),
    )


class PgScoringCalibrationStore:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def latest(self, embedder: str) -> ScoringCalibration | None:
        row = self._conn.execute(
            select(_T).where(_T.c.embedder_name == embedder).order_by(_T.c.version.desc()).limit(1)
        ).first()
        return None if row is None else _calibration(row)

    def save(self, calibration: ScoringCalibration) -> None:
        current = self._conn.execute(
            select(func.coalesce(func.max(_T.c.version), 0)).where(
                _T.c.embedder_name == calibration.embedder
            )
        ).scalar_one()
        if calibration.version != current + 1:
            raise InvariantError(
                f"calibration version {calibration.version} is not the next ({current + 1})"
            )
        with writing(self._conn, f"scoring calibration {calibration.embedder}", NotOwnerError):
            self._conn.execute(
                insert(_T).values(
                    id=calibration.id,
                    version=calibration.version,
                    embedder_name=calibration.embedder,
                    params=_params(calibration),
                )
            )
