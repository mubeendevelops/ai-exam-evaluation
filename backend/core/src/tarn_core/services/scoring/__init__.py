"""Text scoring (P13, design.md "Text scoring (R5)"): criterion scorers, the off-target guard,
answer and booklet scoring. ``tarn_core.services.scoring.ScoringService`` stays the entry point
for one answer."""

from tarn_core.services.scoring.booklet import (
    FAILED_SCORING,
    AnswerApprovedError,
    BookletScorer,
    queue_scoring,
)
from tarn_core.services.scoring.policy import ScoringPolicy
from tarn_core.services.scoring.scorers import ListScorer, NumericScorer, SemanticScorer
from tarn_core.services.scoring.service import (
    MANUAL,
    UNSCORED,
    NoScorerError,
    ScoringContent,
    ScoringService,
    scoring_content,
)

__all__ = [
    "FAILED_SCORING",
    "MANUAL",
    "UNSCORED",
    "AnswerApprovedError",
    "BookletScorer",
    "ListScorer",
    "NoScorerError",
    "NumericScorer",
    "ScoringContent",
    "ScoringPolicy",
    "ScoringService",
    "SemanticScorer",
    "queue_scoring",
    "scoring_content",
]
