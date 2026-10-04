"""P12: segmentation: page order, and what segmentation decided per segment.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-04

- ``pages``: ``written_number`` (the page number the student wrote, when found) and
  ``reading_order`` (the page's place after the page-order check, from 0; ``page_index``
  stays the upload order that names the stored files).
- ``segments``: ``region_ids`` (the page regions it holds, in reading order; no foreign key:
  a region is replaced when a page is re-read, and the segments with it), ``flags``
  (``duplicate``, ``before_first_answer``, ``label_disagrees``, ``number_unread``),
  ``position`` (written order) and ``proposed_label`` (the best question of an unassigned
  segment). ``source`` gets a CHECK.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FLAGS = "'duplicate', 'before_first_answer', 'label_disagrees', 'number_unread'"


def upgrade() -> None:
    for statement in (
        "ALTER TABLE pages ADD COLUMN written_number integer CHECK (written_number >= 1)",
        "ALTER TABLE pages ADD COLUMN reading_order integer CHECK (reading_order >= 0)",
        "ALTER TABLE segments ADD COLUMN region_ids uuid[] NOT NULL DEFAULT '{}'",
        "ALTER TABLE segments ADD COLUMN flags text[] NOT NULL DEFAULT '{}' "
        f"CHECK (flags <@ ARRAY[{_FLAGS}]::text[])",
        "ALTER TABLE segments ADD COLUMN position integer NOT NULL DEFAULT 0 CHECK (position >= 0)",
        "ALTER TABLE segments ADD COLUMN proposed_label text",
        "ALTER TABLE segments ADD CONSTRAINT segment_proposal_only_unassigned "
        "CHECK (proposed_label IS NULL OR slot_label IS NULL)",
        "ALTER TABLE segments ADD CONSTRAINT segment_source "
        "CHECK (source IN ('rule', 'similarity', 'teacher'))",
        "CREATE INDEX ON segments (college_id, booklet_id, position)",
    ):
        op.execute(statement)


def downgrade() -> None:
    for statement in (
        "DROP INDEX IF EXISTS segments_college_id_booklet_id_position_idx",
        "ALTER TABLE segments DROP CONSTRAINT segment_source",
        "ALTER TABLE segments DROP CONSTRAINT segment_proposal_only_unassigned",
        "ALTER TABLE segments DROP COLUMN proposed_label",
        "ALTER TABLE segments DROP COLUMN position",
        "ALTER TABLE segments DROP COLUMN flags",
        "ALTER TABLE segments DROP COLUMN region_ids",
        "ALTER TABLE pages DROP COLUMN reading_order",
        "ALTER TABLE pages DROP COLUMN written_number",
    ):
        op.execute(statement)
