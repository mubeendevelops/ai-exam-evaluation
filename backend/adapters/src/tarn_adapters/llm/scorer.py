"""``LlmScorer``: the LLM as one more ``Scorer`` (P19). One prompt per criterion, one strict JSON
reply, a credit of 0, 1/2 or 1 and a short reason.

It is a *suggestion like any other*: beside the local scorers' credit (a second opinion) or, for
an ``llm`` criterion, the only credit. The mark is still the teacher's to approve."""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Protocol

import structlog

from tarn_adapters.llm.groq import ChatReply
from tarn_adapters.llm.prompts import PROMPT_VERSION, build_messages
from tarn_adapters.llm.reply import BadReplyError, parse_reply
from tarn_core.domain.common import EngineRef
from tarn_core.domain.content import CriterionType
from tarn_core.domain.scoring import CriterionReason, CriterionScore, LlmUsage, SecondOpinion
from tarn_core.errors import ScorerUnavailableError
from tarn_core.ports.engines import ScoringInput

log = structlog.get_logger("tarn_adapters.llm")


class ChatClient(Protocol):
    def chat(self, messages: Sequence[Mapping[str, str]]) -> ChatReply:
        """Raises ``ScorerUnavailableError`` after its own retries."""
        ...


class LlmScorer:
    def __init__(
        self,
        client: ChatClient,
        *,
        model: str,
        max_answer_chars: int = 6000,
        max_replies: int = 2,
    ) -> None:
        """``max_replies``: how often a reply that is not the strict JSON is asked for again."""
        self._client = client
        self._max_answer_chars = max_answer_chars
        self._max_replies = max_replies
        self._ref = EngineRef(name="llm-groq", version=f"{PROMPT_VERSION}+{model}")

    @property
    def ref(self) -> EngineRef:
        return self._ref

    def supports(self, criterion_type: CriterionType) -> bool:
        """Everything but drawings: the model reads text only."""
        return criterion_type is not CriterionType.DIAGRAM

    def opinion(self, item: ScoringInput) -> tuple[SecondOpinion, LlmUsage]:
        if not item.answer_text.strip():
            # Nothing to judge: no request, no tokens, and no invented credit.
            return SecondOpinion(
                scorer=self._ref, credit=Decimal(0), reason="nothing is written"
            ), LlmUsage()
        messages = build_messages(item, max_answer_chars=self._max_answer_chars)
        usage = LlmUsage()
        for attempt in range(1, self._max_replies + 1):
            reply = self._client.chat(messages)
            usage += reply.usage
            try:
                credit, reason = parse_reply(reply.text)
            except BadReplyError as error:
                log.warning("llm.bad_reply", attempt=attempt, problem=str(error))
                continue
            return SecondOpinion(scorer=self._ref, credit=credit, reason=reason), usage
        raise ScorerUnavailableError("the LLM did not return the strict JSON")

    def score(self, item: ScoringInput) -> CriterionScore:
        opinion, _ = self.opinion(item)
        return CriterionScore(
            criterion=item.criterion.ref,
            weight=item.criterion.weight,
            credit=opinion.credit,
            scorer=opinion.scorer,
            evidence=opinion.reason,
            reason=CriterionReason(summary=opinion.reason),
        )
