"""A scripted LLM scorer for tests: credits come from a table, no network, no text kept."""

from collections.abc import Callable
from decimal import Decimal

from tarn_core.domain.common import EngineRef
from tarn_core.domain.content import CriterionType
from tarn_core.domain.scoring import CriterionScore, LlmUsage, SecondOpinion
from tarn_core.errors import ScorerUnavailableError
from tarn_core.ports.engines import ScoringInput

LLM_REF = EngineRef(name="llm-scripted", version="1")


class ScriptedLlm:
    """``credit_for(item)`` returns the credit (0, 0.5 or 1) or None to fail (unavailable)."""

    ref = LLM_REF

    def __init__(self, credit_for: Callable[[ScoringInput], float | None] | float = 1.0) -> None:
        self._credit_for = credit_for if callable(credit_for) else (lambda _item: credit_for)
        self.asked: list[ScoringInput] = []

    def supports(self, criterion_type: CriterionType) -> bool:
        return criterion_type is not CriterionType.DIAGRAM

    def opinion(self, item: ScoringInput) -> tuple[SecondOpinion, LlmUsage]:
        self.asked.append(item)
        credit = self._credit_for(item)
        if credit is None:
            raise ScorerUnavailableError("scripted failure")
        return (
            SecondOpinion(scorer=LLM_REF, credit=Decimal(str(credit)), reason="scripted reason"),
            LlmUsage(calls=1, input_tokens=100, output_tokens=10),
        )

    def score(self, item: ScoringInput) -> CriterionScore:
        opinion, _ = self.opinion(item)
        return CriterionScore(
            criterion=item.criterion.ref,
            weight=item.criterion.weight,
            credit=opinion.credit,
            scorer=opinion.scorer,
            evidence=opinion.reason,
        )
