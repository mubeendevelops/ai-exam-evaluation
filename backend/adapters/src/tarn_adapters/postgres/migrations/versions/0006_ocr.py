"""P10: best-of-N OCR: what the selector decided per line, the OCR stage of pages, and OCR
calibrations as Tarn-operator content.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-04

- ``pages``: ``text_read`` (the OCR stage marker that makes reading resumable), ``needs_text``
  (every engine failed) and ``ocr_failures`` (``engine:timeout`` / ``engine:error``).
- ``regions``: the line's ``content_class``, ``line_score`` and ``flagged``, the calibration
  versions used (``calibrations``), and table structure: ``parent_id`` (the table, same college,
  removed with it) with ``row_index``/``col_index``.
- ``line_readings``: every term of the selector's score per reading (``p_calibrated``,
  ``agreement``, ``lexicon``, ``weight``, ``score``) and whether it ``competing``.
- ``ocr_calibrations``: one item per engine and ``content_class``. Calibrations are fitted by
  the Tarn operator from ground-truth lines and hold numbers only, so they have no owning
  college: ``owning_college_id`` and ``created_by`` become NULL-only. The application role may
  read them but no longer insert (``tarn_app`` loses INSERT); the migration owner may insert
  (policy ``TO CURRENT_USER``), which ``tarn ocr calibrate`` uses. Versions stay contiguous
  (the existing trigger), rows are never updated or deleted.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tarn_app"
_CLASSES = "('print', 'cursive', 'numeric')"
_SCORE_COLUMNS = ("p_calibrated", "agreement", "lexicon", "weight", "score")


def upgrade() -> None:
    for statement in (
        # pages
        "ALTER TABLE pages ADD COLUMN text_read boolean NOT NULL DEFAULT false",
        "ALTER TABLE pages ADD COLUMN needs_text boolean NOT NULL DEFAULT false",
        "ALTER TABLE pages ADD COLUMN ocr_failures text[] NOT NULL DEFAULT '{}'",
        "ALTER TABLE pages ADD CONSTRAINT page_needs_text_after_reading "
        "CHECK (NOT needs_text OR text_read)",
        # regions
        f"ALTER TABLE regions ADD COLUMN content_class text CHECK (content_class IN {_CLASSES})",
        "ALTER TABLE regions ADD COLUMN line_score double precision "
        "CHECK (line_score BETWEEN 0 AND 1)",
        "ALTER TABLE regions ADD COLUMN flagged boolean NOT NULL DEFAULT false",
        "ALTER TABLE regions ADD COLUMN calibrations jsonb NOT NULL DEFAULT '[]'",
        "ALTER TABLE regions ADD COLUMN parent_id uuid",
        "ALTER TABLE regions ADD COLUMN row_index integer CHECK (row_index >= 0)",
        "ALTER TABLE regions ADD COLUMN col_index integer CHECK (col_index >= 0)",
        "ALTER TABLE regions ADD CONSTRAINT region_cell_position "
        "CHECK ((row_index IS NULL) = (col_index IS NULL) "
        "AND (row_index IS NULL OR parent_id IS NOT NULL) AND parent_id IS DISTINCT FROM id)",
        "ALTER TABLE regions ADD CONSTRAINT region_parent FOREIGN KEY (college_id, parent_id) "
        "REFERENCES regions (college_id, id) ON DELETE CASCADE",
        "CREATE INDEX ON regions (college_id, parent_id) WHERE parent_id IS NOT NULL",
        # line_readings: the selector's terms, all present or none
        *(
            f"ALTER TABLE line_readings ADD COLUMN {c} double precision CHECK ({c} >= 0)"
            for c in _SCORE_COLUMNS
        ),
        "ALTER TABLE line_readings ADD COLUMN competing boolean",
        "ALTER TABLE line_readings ADD CONSTRAINT reading_score_complete CHECK ("
        + " AND ".join(f"(({c} IS NULL) = (score IS NULL))" for c in _SCORE_COLUMNS)
        + " AND ((competing IS NULL) = (score IS NULL)))",
        "ALTER TABLE line_readings ADD CONSTRAINT reading_unit_terms CHECK ("
        "p_calibrated <= 1 AND agreement <= 1 AND lexicon <= 1)",
        # ocr_calibrations: Tarn-operator content
        "ALTER TABLE ocr_calibrations ADD COLUMN content_class text NOT NULL "
        f"CHECK (content_class IN {_CLASSES})",
        "ALTER TABLE ocr_calibrations ALTER COLUMN owning_college_id DROP NOT NULL",
        "ALTER TABLE ocr_calibrations ALTER COLUMN created_by DROP NOT NULL",
        "ALTER TABLE ocr_calibrations ADD CONSTRAINT calibration_has_no_owner "
        "CHECK (owning_college_id IS NULL AND created_by IS NULL)",
        "CREATE UNIQUE INDEX ON ocr_calibrations (engine_name, content_class, version)",
        "DROP POLICY owner_insert ON ocr_calibrations",
        f"REVOKE INSERT ON ocr_calibrations FROM {APP_ROLE}",
        "CREATE POLICY operator_insert ON ocr_calibrations FOR INSERT TO CURRENT_USER "
        "WITH CHECK (owning_college_id IS NULL)",
    ):
        op.execute(statement)


def downgrade() -> None:
    for statement in (
        "DROP POLICY operator_insert ON ocr_calibrations",
        f"GRANT INSERT ON ocr_calibrations TO {APP_ROLE}",
        "CREATE POLICY owner_insert ON ocr_calibrations FOR INSERT "
        "WITH CHECK (owning_college_id = tarn_current_college())",
        # Development only: operator rows have no owner and cannot survive the old schema.
        "DELETE FROM ocr_calibrations",
        "ALTER TABLE ocr_calibrations DROP CONSTRAINT calibration_has_no_owner",
        "ALTER TABLE ocr_calibrations ALTER COLUMN created_by SET NOT NULL",
        "ALTER TABLE ocr_calibrations ALTER COLUMN owning_college_id SET NOT NULL",
        "ALTER TABLE ocr_calibrations DROP COLUMN content_class",
        "ALTER TABLE line_readings DROP CONSTRAINT reading_unit_terms",
        "ALTER TABLE line_readings DROP CONSTRAINT reading_score_complete",
        "ALTER TABLE line_readings DROP COLUMN competing",
        *(f"ALTER TABLE line_readings DROP COLUMN {c}" for c in _SCORE_COLUMNS),
        "ALTER TABLE regions DROP CONSTRAINT region_parent",
        "ALTER TABLE regions DROP CONSTRAINT region_cell_position",
        "ALTER TABLE regions DROP COLUMN col_index",
        "ALTER TABLE regions DROP COLUMN row_index",
        "ALTER TABLE regions DROP COLUMN parent_id",
        "ALTER TABLE regions DROP COLUMN calibrations",
        "ALTER TABLE regions DROP COLUMN flagged",
        "ALTER TABLE regions DROP COLUMN line_score",
        "ALTER TABLE regions DROP COLUMN content_class",
        "ALTER TABLE pages DROP CONSTRAINT page_needs_text_after_reading",
        "ALTER TABLE pages DROP COLUMN ocr_failures",
        "ALTER TABLE pages DROP COLUMN needs_text",
        "ALTER TABLE pages DROP COLUMN text_read",
    ):
        op.execute(statement)
