"""P14: diagram recognition and comparison.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-05

- ``reference_diagrams.kind`` (flowchart, block, network, tree; circuit, plot and
  labelled_drawing wait for their plug-ins) and ``recognition`` (pending until the recognizer
  has read the PNG, then recognised / failed / edited). Global rows stay append-only: a change
  is the next version.
- ``diagram_graphs.region_id``: the page's diagram region the drawing was read from (one
  drawing per region; NULL for a graph the teacher drew), and the drawing's ``kind``.
- ``criterion_scores.detail``: the R6 comparison document of a diagram criterion
  (``docs/api/diagram-comparison.schema.json``). Insert-only like the rest of the score.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KINDS = "'flowchart', 'block', 'network', 'tree', 'circuit', 'plot', 'labelled_drawing'"
_STATES = "'pending', 'recognised', 'failed', 'edited'"


def upgrade() -> None:
    for statement in (
        "ALTER TABLE reference_diagrams ADD COLUMN kind text NOT NULL DEFAULT 'flowchart' "
        f"CHECK (kind IN ({_KINDS}))",
        "ALTER TABLE reference_diagrams ADD COLUMN recognition text NOT NULL DEFAULT 'pending' "
        f"CHECK (recognition IN ({_STATES}))",
        "ALTER TABLE diagram_graphs ADD COLUMN region_id uuid",
        "ALTER TABLE diagram_graphs ADD CONSTRAINT diagram_graphs_region_fk "
        "FOREIGN KEY (college_id, region_id) REFERENCES regions (college_id, id) "
        "ON DELETE SET NULL (region_id)",
        "CREATE UNIQUE INDEX diagram_graphs_one_per_region ON diagram_graphs "
        "(college_id, region_id) WHERE region_id IS NOT NULL",
        "ALTER TABLE diagram_graphs ADD COLUMN kind text NOT NULL DEFAULT 'flowchart' "
        f"CHECK (kind IN ({_KINDS}))",
        "ALTER TABLE criterion_scores ADD COLUMN detail jsonb "
        "CHECK (detail IS NULL OR jsonb_typeof(detail) = 'object')",
    ):
        op.execute(statement)


def downgrade() -> None:
    for statement in (
        "ALTER TABLE criterion_scores DROP COLUMN detail",
        "ALTER TABLE diagram_graphs DROP COLUMN kind",
        "DROP INDEX diagram_graphs_one_per_region",
        "ALTER TABLE diagram_graphs DROP CONSTRAINT diagram_graphs_region_fk",
        "ALTER TABLE diagram_graphs DROP COLUMN region_id",
        "ALTER TABLE reference_diagrams DROP COLUMN recognition",
        "ALTER TABLE reference_diagrams DROP COLUMN kind",
    ):
        op.execute(statement)
