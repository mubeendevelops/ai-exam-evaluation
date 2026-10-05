"""P13: text scoring.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-05

- ``regions.struck_out``: the student struck the line out; scoring leaves it out (set by the
  teacher until strike-outs are detected, O58).
- ``glossaries.off_target_terms``: names that contradict the key (the off-target guard).
- ``answer_scores``: ``mark`` may be NULL only for a "mark manually" score (guidance-only key);
  ``flags`` (low_ocr, off_target, blank, mark_manually, duplicate), ``relevance``, the
  embedder used, and answer-level ``reasons``.
- ``criterion_scores``: ``similarity`` (semantic criteria) and the structured ``reason`` shown
  in Panel B.
- ``sentence_embeddings``: the vector column gets the scoring model's dimension (384,
  ``thenlper/gte-small``, D99) and each row the SHA-256 of its sentence, so a re-score reuses
  unchanged sentences. The table is a cache: existing rows are dropped.
- ``scoring_calibrations``: Tarn-operator content like ``ocr_calibrations`` (numbers only, no
  owning college): every college reads; only the migration owner (``tarn score calibrate``)
  inserts; versions never change.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tarn_app"
DIMENSION = 384
_ANSWER_FLAGS = "'low_ocr', 'off_target', 'blank', 'mark_manually', 'duplicate'"


def upgrade() -> None:
    for statement in (
        "ALTER TABLE regions ADD COLUMN struck_out boolean NOT NULL DEFAULT false",
        "ALTER TABLE glossaries ADD COLUMN off_target_terms text[] NOT NULL DEFAULT '{}'",
        # answer scores
        "ALTER TABLE answer_scores ALTER COLUMN mark DROP NOT NULL",
        "ALTER TABLE answer_scores ADD COLUMN flags text[] NOT NULL DEFAULT '{}' "
        f"CHECK (flags <@ ARRAY[{_ANSWER_FLAGS}]::text[])",
        "ALTER TABLE answer_scores ADD CONSTRAINT score_mark_unless_manual "
        "CHECK ((mark IS NULL) = ('mark_manually' = ANY (flags)))",
        "ALTER TABLE answer_scores ADD COLUMN relevance double precision "
        "CHECK (relevance BETWEEN 0 AND 1)",
        "ALTER TABLE answer_scores ADD COLUMN embedder_name text",
        "ALTER TABLE answer_scores ADD COLUMN embedder_version text",
        "ALTER TABLE answer_scores ADD CONSTRAINT score_embedder_named "
        "CHECK ((embedder_name IS NULL) = (embedder_version IS NULL))",
        "ALTER TABLE answer_scores ADD COLUMN reasons text[] NOT NULL DEFAULT '{}'",
        "ALTER TABLE criterion_scores ADD COLUMN similarity double precision",
        "ALTER TABLE criterion_scores ADD COLUMN reason jsonb",
        # sentence vectors: a cache, typed to the scoring model's dimension
        "TRUNCATE sentence_embeddings",
        "ALTER TABLE sentence_embeddings DROP COLUMN dimension",
        f"ALTER TABLE sentence_embeddings ALTER COLUMN embedding TYPE vector({DIMENSION})",
        "ALTER TABLE sentence_embeddings ADD COLUMN text_sha256 text NOT NULL "
        "CHECK (text_sha256 ~ '^[0-9a-f]{64}$')",
        "CREATE INDEX ON sentence_embeddings (college_id, answer_id)",
        # scoring calibrations: Tarn-operator content
        """CREATE TABLE scoring_calibrations (
            id uuid NOT NULL,
            version integer NOT NULL CHECK (version >= 1),
            embedder_name text NOT NULL,
            params jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (id, version),
            UNIQUE (embedder_name, version)
        )""",
        "ALTER TABLE scoring_calibrations ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE scoring_calibrations FORCE ROW LEVEL SECURITY",
        "CREATE POLICY global_read ON scoring_calibrations FOR SELECT USING (true)",
        "CREATE POLICY operator_insert ON scoring_calibrations FOR INSERT TO CURRENT_USER "
        "WITH CHECK (true)",
        f"GRANT SELECT ON scoring_calibrations TO {APP_ROLE}",
        "CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON scoring_calibrations "
        "FOR EACH ROW EXECUTE FUNCTION tarn_append_only()",
    ):
        op.execute(statement)


def downgrade() -> None:
    for statement in (
        "DROP TABLE scoring_calibrations",
        "DROP INDEX IF EXISTS sentence_embeddings_college_id_answer_id_idx",
        "TRUNCATE sentence_embeddings",
        "ALTER TABLE sentence_embeddings DROP COLUMN text_sha256",
        "ALTER TABLE sentence_embeddings ALTER COLUMN embedding TYPE vector",
        "ALTER TABLE sentence_embeddings ADD COLUMN dimension integer NOT NULL "
        "CHECK (dimension > 0)",
        "ALTER TABLE sentence_embeddings ADD CHECK (vector_dims(embedding) = dimension)",
        "ALTER TABLE criterion_scores DROP COLUMN reason",
        "ALTER TABLE criterion_scores DROP COLUMN similarity",
        "ALTER TABLE answer_scores DROP COLUMN reasons",
        "ALTER TABLE answer_scores DROP CONSTRAINT score_embedder_named",
        "ALTER TABLE answer_scores DROP COLUMN embedder_version",
        "ALTER TABLE answer_scores DROP COLUMN embedder_name",
        "ALTER TABLE answer_scores DROP COLUMN relevance",
        "ALTER TABLE answer_scores DROP CONSTRAINT score_mark_unless_manual",
        "ALTER TABLE answer_scores DROP COLUMN flags",
        # Development only: "mark manually" scores have no mark and cannot survive.
        "ALTER TABLE answer_scores NO FORCE ROW LEVEL SECURITY",
        "DELETE FROM answer_scores WHERE mark IS NULL",
        "ALTER TABLE answer_scores FORCE ROW LEVEL SECURITY",
        "ALTER TABLE answer_scores ALTER COLUMN mark SET NOT NULL",
        "ALTER TABLE glossaries DROP COLUMN off_target_terms",
        "ALTER TABLE regions DROP COLUMN struck_out",
    ):
        op.execute(statement)
