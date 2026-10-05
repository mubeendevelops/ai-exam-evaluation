"""Calibration (P13): features, band and flag fitting, cross-validation, the accuracy report.
Synthetic answers and a scripted embedder whose cosines the test chooses."""

from collections.abc import Sequence
from decimal import Decimal
from uuid import uuid4

import pytest

from tarn_core.domain.common import EngineRef
from tarn_core.domain.content import (
    ContentMeta,
    CriterionType,
    ListItem,
    ListParams,
    RubricCriterion,
    SemanticParams,
)
from tarn_core.ids import CollegeId, CriterionId, QuestionId, UserId
from tarn_core.services.scoring.calibration import (
    CalibrationQuestion,
    Features,
    MarkedAnswer,
    ModelResult,
    accuracy,
    balanced_mae,
    best_constant,
    cross_validated,
    features,
    fit,
    fit_bands,
    mark_group,
    mean_abs_diff,
    render_accuracy,
    render_models,
    spearman,
)
from tarn_core.services.scoring.policy import ScoringPolicy

META = ContentMeta(owning_college_id=CollegeId(uuid4()), created_by=UserId(uuid4()))
STATEMENT = "reference statement"


class Scripted:
    """'s=0.73 ...' embeds at cosine 0.73 to the statement; anything else at 0."""

    ref = EngineRef(name="scripted", version="1")
    dimension = 2

    def embed(self, texts: Sequence[str]) -> Sequence[tuple[float, ...]]:
        out: list[tuple[float, ...]] = []
        for t in texts:
            c = 1.0 if t == STATEMENT else float(t[2:6]) if t.startswith("s=") else 0.0
            out.append((c, (1 - c * c) ** 0.5))
        return out


def question(code: str, *, listed: bool = False) -> CalibrationQuestion:
    criteria = [
        RubricCriterion(
            id=CriterionId(uuid4()),
            meta=META,
            question_id=QuestionId(uuid4()),
            label="point",
            type=CriterionType.SEMANTIC,
            weight=Decimal(2),
            params=SemanticParams(reference_statement=STATEMENT),
        )
    ]
    if listed:
        criteria.append(
            RubricCriterion(
                id=CriterionId(uuid4()),
                meta=META,
                question_id=criteria[0].question_id,
                label="items",
                type=CriterionType.LIST,
                weight=Decimal(1),
                params=ListParams(items=(ListItem(term="photosynthesis"),), required_count=1),
            )
        )
    return CalibrationQuestion(
        code=code,
        text="Explain the reference point.",
        max_marks=sum((c.weight for c in criteria), Decimal(0)),
        criteria=tuple(criteria),
        key_texts=(STATEMENT,),
        off_target_terms=("Congress",),
    )


def answer(code: str, cosine: float, mark: str, extra: str = "") -> MarkedAnswer:
    return MarkedAnswer(
        question=code,
        text=f"s={cosine:.2f} written answer words here{extra}",
        teacher_mark=Decimal(mark),
    )


# The teacher gives 2 above 0.70, 1 between 0.40 and 0.70, 0 below.
MARKED = [
    answer(code, cosine, mark)
    for code in ("Q1", "Q2", "Q3", "Q4")
    for cosine, mark in ((0.85, "2"), (0.75, "2"), (0.6, "1"), (0.5, "1"), (0.3, "0"), (0.1, "0"))
]


def test_features_hold_numbers_not_text() -> None:
    given = [answer("Q1", 0.6, "2", ". photosynthesis")]
    items = features([question("Q1", listed=True)], given, Scripted())
    (f,) = items
    assert f.semantic[0][0] == Decimal(2)
    assert f.semantic[0][1] == pytest.approx(0.6, abs=1e-6)
    assert f.fixed == ((Decimal(1), Decimal(1)),)
    assert f.words >= 3 and not f.contradicting
    assert "written" not in repr(f)  # no answer text kept


def test_answers_to_unknown_questions_are_skipped() -> None:
    assert features([question("Q1")], [answer("Q9", 0.5, "1")], Scripted()) == []


def test_contradicting_terms_are_a_feature() -> None:
    (f,) = features([question("Q1")], [answer("Q1", 0.9, "0", ". Congress passed it")], Scripted())
    assert f.contradicting
    assert f.flagged(ScoringPolicy())


def test_band_fitting_recovers_the_teachers_edges() -> None:
    items = features([question(c) for c in ("Q1", "Q2", "Q3", "Q4")], MARKED, Scripted())
    default = ScoringPolicy(half=0.2, full=0.95)
    assert mean_abs_diff(items, default) > 0
    fitted = fit_bands(items)
    assert mean_abs_diff(items, fitted) == 0
    assert 0.3 < fitted.half <= 0.5 and 0.6 < fitted.full <= 0.75


def test_cross_validation_is_grouped_by_question() -> None:
    items = features([question(c) for c in ("Q1", "Q2", "Q3", "Q4")], MARKED, Scripted())
    assert cross_validated(items, folds=2) == (0, 0)  # every question follows the same rule


def test_flag_fit_catches_disagreements_within_the_flag_budget() -> None:
    questions = [question(c) for c in ("Q1", "Q2", "Q3", "Q4")]
    # Two answers the bands cannot get right: the teacher disagrees with their similarity.
    odd = [answer("Q1", 0.72, "0"), answer("Q2", 0.71, "0")]
    items = features(questions, [*MARKED, *odd], Scripted())
    result = fit(items, max_flag_rate=0.3)
    assert result.rate <= 0.3
    assert result.recall == 1.0  # both sit just above the full edge: caught by the margin
    assert result.policy.margin > 0


def test_accuracy_report_per_question() -> None:
    items = features([question("Q1"), question("Q2")], MARKED[:12], Scripted())
    policy = ScoringPolicy(half=0.4, full=0.7)
    report = accuracy(items, policy)
    assert [r.code for r in report.rows] == ["Q1", "Q2"]
    assert report.overall.answers == 12
    assert report.overall.mean_abs_diff == 0 and report.overall.within_half == 1.0
    off = accuracy(items, ScoringPolicy(half=0.4, full=0.9))  # 0.85 and 0.75 now get 1, not 2
    assert off.rows[0].mean_abs_diff == pytest.approx(2 / 6)
    assert off.rows[0].within_half == pytest.approx(4 / 6)


def test_rendered_reports_hold_codes_and_numbers_only() -> None:
    items = features([question("Q1")], MARKED[:6], Scripted())
    policy = ScoringPolicy(half=0.4, full=0.7)
    text = render_accuracy(
        "Scoring accuracy", ["note"], accuracy(items, policy), policy, flag_rate=0.1
    )
    assert "| Q1 | 6 | 2 | 0.00 | 100% |" in text
    assert "written" not in text
    models = render_models(
        "Models",
        [],
        [
            ModelResult(
                model="b",
                answers=6,
                spearman=0.5,
                cv_mae=0.9,
                fitted_balanced=0.8,
                cv_balanced=0.9,
                seconds_per_answer=0.1,
            ),
            ModelResult(
                model="a",
                answers=6,
                spearman=0.9,
                cv_mae=0.2,
                fitted_balanced=0.1,
                cv_balanced=0.2,
                seconds_per_answer=0.2,
            ),
        ],
    )
    assert models.index("| a |") < models.index("| b |")  # best first


@pytest.mark.parametrize(
    ("xs", "ys", "rho"),
    [([1, 2, 3, 4], [10, 20, 30, 40], 1.0), ([1, 2, 3, 4], [4, 3, 2, 1], -1.0),
     ([1, 1, 1], [1, 2, 3], 0.0), ([1, 2, 2, 3], [1, 2, 3, 4], 0.9486832980505138)],
)  # fmt: skip
def test_spearman(xs: list[float], ys: list[float], rho: float) -> None:
    assert spearman(xs, ys) == pytest.approx(rho)


def test_blank_answers_score_zero_and_are_flagged() -> None:
    f = Features(
        question="Q1",
        teacher_mark=Decimal(0),
        max_marks=Decimal(2),
        mark_step=Decimal("0.5"),
        fixed=((Decimal(1), Decimal(1)),),
        semantic=(),
        relevance=None,
        contradicting=False,
        unfamiliar=0,
        words=1,
    )
    assert f.mark(ScoringPolicy()) == 0 and f.flagged(ScoringPolicy())


def test_skewed_marks_are_fitted_on_the_balanced_difference() -> None:
    """Most answers earn full marks (like the Mohler set): plain mean difference would fit
    'full credit for everything'; the balanced fit keeps the weak answers low."""
    questions = [question(c) for c in ("Q1", "Q2", "Q3", "Q4")]
    strong = [answer(c, 0.9, "2") for c in ("Q1", "Q2", "Q3", "Q4") for _ in range(8)]
    weak = [answer(c, cos, "0") for c, cos in (("Q1", 0.3), ("Q2", 0.35), ("Q3", 0.3))]
    middling = [answer("Q4", 0.6, "1")]
    items = features(questions, [*strong, *weak, *middling], Scripted())
    everything = ScoringPolicy(half=0.0, full=0.05)  # full credit whatever is written
    fitted = fit_bands(items)
    assert balanced_mae(items, fitted) < balanced_mae(items, everything)
    assert all(f.mark(fitted) == 0 for f in items if f.teacher_mark == 0)
    baseline = best_constant(items)
    # "Give everyone full marks" (0.9 x 2 rounds to 2 too) is the best constant guess...
    assert baseline.share >= Decimal("0.9")
    assert baseline.balanced > balanced_mae(items, fitted)  # ...and the model beats it balanced
    assert mark_group(items[0]) == 2 and mark_group(items[-1]) == 1


def test_relevance_thresholds_come_from_genuine_answers() -> None:
    """Off-target = less relevant than nearly all of the set's answers to their questions."""
    questions = [question(c) for c in ("Q1", "Q2", "Q3", "Q4")]
    items = features(questions, MARKED, Scripted())
    relevances = sorted(f.relevance for f in items if f.relevance is not None)
    result = fit(items)
    assert result.policy.relevance_min == round(relevances[0], 3)  # 2nd percentile of 24
    assert relevances[0] <= result.policy.relevance_soft <= relevances[-1]
