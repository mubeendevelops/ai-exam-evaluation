"""P19: the LLM scorer (off by default).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-07

- ``colleges.llm_scoring``: the per-college feature flag. Off by default; a Tarn operator turns
  it on (``tarn tenants llm``) because answer text then goes to the LLM provider.
- ``criterion_scores.second_opinion``: the LLM's credit and reason beside the non-LLM credit
  (JSON object, college data like the rest of the score).
- ``answer_scores.llm_usage``: calls and input/output token counts the LLM cost for this score
  (counts only), NULL when the LLM was not used.
- ``answer_scores.flags`` accepts ``scorer_disagreement``.
Row-level security is unchanged: both tables already filter on ``college_id``.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_FLAGS = "'low_ocr', 'off_target', 'blank', 'mark_manually', 'duplicate'"
_NEW_FLAGS = f"{_OLD_FLAGS}, 'scorer_disagreement'"


def upgrade() -> None:
    for statement in (
        "ALTER TABLE colleges ADD COLUMN llm_scoring boolean NOT NULL DEFAULT false",
        "ALTER TABLE criterion_scores ADD COLUMN second_opinion jsonb "
        "CHECK (second_opinion IS NULL OR jsonb_typeof(second_opinion) = 'object')",
        "ALTER TABLE answer_scores ADD COLUMN llm_usage jsonb "
        "CHECK (llm_usage IS NULL OR jsonb_typeof(llm_usage) = 'object')",
        "ALTER TABLE answer_scores DROP CONSTRAINT answer_scores_flags_check",
        f"ALTER TABLE answer_scores ADD CONSTRAINT answer_scores_flags_check "
        f"CHECK (flags <@ ARRAY[{_NEW_FLAGS}]::text[])",
    ):
        op.execute(statement)


def downgrade() -> None:
    for statement in (
        "DELETE FROM answer_scores WHERE 'scorer_disagreement' = ANY (flags)",
        "ALTER TABLE answer_scores DROP CONSTRAINT answer_scores_flags_check",
        f"ALTER TABLE answer_scores ADD CONSTRAINT answer_scores_flags_check "
        f"CHECK (flags <@ ARRAY[{_OLD_FLAGS}]::text[])",
        "ALTER TABLE answer_scores DROP COLUMN llm_usage",
        "ALTER TABLE criterion_scores DROP COLUMN second_opinion",
        "ALTER TABLE colleges DROP COLUMN llm_scoring",
    ):
        op.execute(statement)
