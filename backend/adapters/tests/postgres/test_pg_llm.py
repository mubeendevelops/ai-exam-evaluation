"""The LLM scorer on PostgreSQL (P19): the college flag (off by default, one college's switch
does not touch another's), a second opinion and the usage stored with the score, the
``scorer_disagreement`` flag accepted by the database, and the audit event that names the
scorers."""

from typing import Any

import pytest
from sqlalchemy import text

from tarn_adapters.postgres.testing import Opener, World
from tarn_core.domain.audit import AuditAction
from tarn_core.domain.scoring import DISAGREE, AnswerFlag, LlmUsage
from tarn_core.services.college_settings import CollegeSettings
from tarn_core.services.scoring import ScoringPolicy, ScoringService
from tarn_core.services.segmentation.similarity import TrigramEmbedder
from tarn_core.testing.llm import LLM_REF, ScriptedLlm

pytestmark = pytest.mark.integration

GOOD = "alpha and beta. The idea follows from alpha and beta."


def _service(s: Any, llm: ScriptedLlm) -> ScoringService:
    return ScoringService.standard(
        booklets=s.booklets,
        scores=s.scores,
        content=s.content,
        runtime=s.runtime,
        embedder=TrigramEmbedder(),
        policy=ScoringPolicy(half=0.25, full=0.45, relevance_min=0.0, relevance_soft=0.0),
        llm=llm,
        colleges=s.colleges,
    )


def test_the_flag_is_off_until_an_operator_switches_it_on(session: Opener, world: World) -> None:
    a, b = world.a, world.b
    with session(a.id) as s:
        assert s.colleges.get(a.id).llm_scoring is False
        CollegeSettings(colleges=s.colleges, runtime=s.runtime).set_llm_scoring(
            a.id, True, operator="Ops One"
        )
        assert s.colleges.get(a.id).llm_scoring is True
        events = s.conn.execute(
            text("SELECT after FROM audit_events WHERE action = :a"),
            {"a": AuditAction.COLLEGE_LLM_SCORING_SET.value},
        ).all()
        assert [e.after for e in events] == [{"llm_scoring": True, "operator": "Ops One"}]
    with session(b.id) as s:
        assert s.colleges.get(b.id).llm_scoring is False  # B is untouched
    with session(a.id) as s:
        CollegeSettings(colleges=s.colleges, runtime=s.runtime).set_llm_scoring(
            a.id, False, operator="Ops One"
        )
        assert s.colleges.get(a.id).llm_scoring is False


def test_a_second_opinion_is_stored_flagged_and_named_in_the_audit_log(
    session: Opener, world: World
) -> None:
    a = world.a
    answer = next(x for x in a.answers if x.slot_label == "2")
    with session(a.id) as s:
        # Off: the model is never asked, the score has no opinion and no usage.
        llm = ScriptedLlm(0.0)
        plain = _service(s, llm).score_answer(
            a.id, a.college.teacher.id, answer.id, answer_text=GOOD
        )
        assert llm.asked == [] and plain.llm_usage is None
        CollegeSettings(colleges=s.colleges, runtime=s.runtime).set_llm_scoring(
            a.id, True, operator="Ops One"
        )
    with session(a.id) as s:
        llm = ScriptedLlm(lambda item: 0.0 if item.criterion.label.startswith("Names") else 0.5)
        score = _service(s, llm).score_answer(
            a.id, a.college.teacher.id, answer.id, answer_text=GOOD
        )
        assert len(llm.asked) == len(score.criterion_scores) == 2
    with session(a.id) as s:
        stored = s.scores.scores(a.id, answer.id)[-1]
        assert stored == score
        assert stored.llm_usage == LlmUsage(calls=2, input_tokens=200, output_tokens=20)
        assert all(c.second_opinion is not None for c in stored.criterion_scores)
        assert LLM_REF in stored.scorer_versions
        # Full credit on the list criterion against the model's 0: more than one band apart.
        # The semantic one differs by half a band at most: not a disagreement.
        assert [DISAGREE in c.flags for c in stored.criterion_scores] == [True, False]
        assert AnswerFlag.SCORER_DISAGREEMENT in stored.flags
        assert stored.mark == plain.mark  # the model never changes the mark

        after = s.conn.execute(
            text(
                "SELECT after FROM audit_events WHERE action = 'answer.scored' "
                "AND answer_id = :id AND jsonb_typeof(after -> 'llm_usage') = 'object'"
            ),
            {"id": answer.id},
        ).scalar_one()
        assert after["llm_usage"] == {"calls": 2, "input": 200, "output": 20}
        assert {t["second_opinion"] for t in after["scorers"]} == {"llm-scripted 1"}
        assert "scripted reason" not in str(after)  # model text stays in the score row only

    with session(world.b.id) as s:
        assert s.scores.scores(world.b.id, answer.id) == []  # row-level security
