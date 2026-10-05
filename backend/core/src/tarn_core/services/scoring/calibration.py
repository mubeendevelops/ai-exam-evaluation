"""Fitting the scoring thresholds on teacher-marked answers, and the accuracy report
(design.md "How accuracy is reported": per question, mean absolute difference from the
teacher's mark and the share of answers within half a mark of it).

Each answer is reduced once to its *features*: the credit of every list and numeric criterion
(they do not depend on the thresholds), the best similarity of every semantic criterion, and
the off-target guard's raw signals. Marks and flags under any candidate ``ScoringPolicy`` are
then plain arithmetic, so a grid search over the bands is cheap.

* Bands (``half``, ``full``): the pair with the lowest *balanced* mean absolute difference: the
  mean of the differences in the low, middle and high thirds of the teachers' marks. Plain mean
  difference rewards "full credit for everything" when most answers earn high marks (on the
  Mohler set a constant 4.5 of 5 beats every model on it), so it is reported, not fitted.
  Every report shows the best constant guess beside the model for that reason.
* Borderline ``margin``: the most disagreements caught (AI and teacher more than half a mark
  apart) while flagging at most ``max_flag_rate`` of the answers ("flags decide where the
  teacher should look first", design.md).
* Off-target relevance (``relevance_min``, ``relevance_soft``): the 2nd and 10th percentiles of
  the relevance of the set's answers to their own questions: off-target = less relevant than
  nearly every genuine answer. Marks cannot fit it: a set has (almost) no off-target answers
  to learn from, and relevance does not predict an on-topic answer's mark.

Numbers and question codes only leave this module: no answer text."""

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal

from tarn_core.domain.content import (
    CriterionType,
    ListParams,
    NumericParams,
    RubricCriterion,
    SemanticParams,
)
from tarn_core.domain.scoring import round_to_step
from tarn_core.errors import InvariantError
from tarn_core.ports.engines import Embedder, ScoringInput, WordList
from tarn_core.services.scoring.guard import OffTargetGuard
from tarn_core.services.scoring.policy import ScoringPolicy
from tarn_core.services.scoring.scorers import ListScorer, NumericScorer, Vector, best_match
from tarn_core.services.scoring.text import split_sentences, tokens, word_count

HALF_MARK = Decimal("0.5")


@dataclass(frozen=True, slots=True, kw_only=True)
class CalibrationQuestion:
    """A question as the scorers see it: its rubric, its usable keys and off-target terms."""

    code: str
    text: str
    max_marks: Decimal
    criteria: tuple[RubricCriterion, ...]
    key_texts: tuple[str, ...] = ()
    off_target_terms: tuple[str, ...] = ()
    mark_step: Decimal = HALF_MARK

    def __post_init__(self) -> None:
        if not self.criteria:
            raise InvariantError(f"{self.code}: a calibration question needs criteria")
        total = sum((c.weight for c in self.criteria), Decimal(0))
        if total != self.max_marks:
            raise InvariantError(f"{self.code}: criteria add up to {total}, not {self.max_marks}")


@dataclass(frozen=True, slots=True, kw_only=True)
class MarkedAnswer:
    question: str
    """The question's code."""
    text: str
    teacher_mark: Decimal
    marked_by: str = "teacher"
    """``teacher``, ``dataset`` (human marks of a public set) or ``claude`` (provisional)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Features:
    question: str
    teacher_mark: Decimal
    max_marks: Decimal
    mark_step: Decimal
    fixed: tuple[tuple[Decimal, Decimal], ...]
    """(weight, credit) of list and numeric criteria."""
    semantic: tuple[tuple[Decimal, float], ...]
    """(weight, best similarity) of semantic criteria."""
    relevance: float | None
    contradicting: bool
    unfamiliar: int
    words: int

    def mark(self, policy: ScoringPolicy) -> Decimal:
        if self.words < policy.blank_words:
            return Decimal(0)
        total = sum((w * c for w, c in self.fixed), Decimal(0))
        for weight, similarity in self.semantic:
            credit, _ = policy.band(similarity)
            total += weight * Decimal(str(credit))
        return round_to_step(total, self.mark_step)

    def flagged(self, policy: ScoringPolicy) -> bool:
        if self.words < policy.blank_words:
            return True  # blank: always shown for confirmation
        if any(policy.band(s)[1] for _, s in self.semantic):
            return True
        if self.contradicting:
            return True
        if self.relevance is None:
            return False
        if self.relevance < policy.relevance_min:
            return True
        return bool(self.unfamiliar) and self.relevance < policy.relevance_soft


def features(
    questions: Sequence[CalibrationQuestion],
    answers: Iterable[MarkedAnswer],
    embedder: Embedder,
    *,
    word_list: WordList | None = None,
) -> list[Features]:
    """Score every answer once. Answers to unknown questions are skipped."""
    by_code = {q.code: q for q in questions}
    vocabulary = frozenset(
        w
        for q in questions
        for text in (q.text, *q.key_texts, *(_criterion_text(c) for c in q.criteria))
        for w in tokens(text)
    )
    guard = OffTargetGuard(embedder, ScoringPolicy(), word_list=word_list)
    statements: dict[str, Vector] = {}
    lister, numeric = ListScorer(), NumericScorer()
    out: list[Features] = []
    for answer in answers:
        q = by_code.get(answer.question)
        if q is None:
            continue
        sentences = tuple(split_sentences(answer.text.splitlines()))
        vectors = [tuple(v) for v in embedder.embed(list(sentences))] if sentences else []
        fixed: list[tuple[Decimal, Decimal]] = []
        semantic: list[tuple[Decimal, float]] = []
        for c in q.criteria:
            if isinstance(c.params, SemanticParams):
                statement = c.params.reference_statement
                if statement not in statements:
                    statements[statement] = tuple(embedder.embed([statement])[0])
                similarity = best_match(statements[statement], vectors)[0] if vectors else 0.0
                semantic.append((c.weight, similarity))
            elif isinstance(c.params, (ListParams, NumericParams)):
                scorer = lister if c.type is CriterionType.LIST else numeric
                given = ScoringInput(criterion=c, answer_text=answer.text, sentences=sentences)
                fixed.append((c.weight, scorer.score(given).credit))
            else:  # diagrams and LLM criteria are the teacher's until their scorers exist
                fixed.append((c.weight, Decimal(0)))
        statements_used = [
            c.params.reference_statement for c in q.criteria if isinstance(c.params, SemanticParams)
        ]
        signal = guard.check(
            sentences,
            vectors,
            key_texts=[q.text, *q.key_texts, *statements_used],
            off_target_terms=q.off_target_terms,
            vocabulary=vocabulary,
        )
        out.append(
            Features(
                question=q.code,
                teacher_mark=answer.teacher_mark,
                max_marks=q.max_marks,
                mark_step=q.mark_step,
                fixed=tuple(fixed),
                semantic=tuple(semantic),
                relevance=signal.relevance,
                contradicting=bool(signal.contradicting),
                unfamiliar=signal.unfamiliar,
                words=word_count(sentences),
            )
        )
    return out


def _criterion_text(c: RubricCriterion) -> str:
    p = c.params
    if isinstance(p, ListParams):
        return " ".join(t for i in p.items for t in (i.term, *i.synonyms))
    if isinstance(p, SemanticParams):
        return p.reference_statement
    return c.label


# --- fitting ----------------------------------------------------------------------------------


def _grid(lo: float, hi: float, step: float) -> list[float]:
    n = round((hi - lo) / step)
    return [round(lo + k * step, 4) for k in range(n + 1)]


def mean_abs_diff(items: Sequence[Features], policy: ScoringPolicy) -> float:
    if not items:
        return 0.0
    return float(sum(abs(f.mark(policy) - f.teacher_mark) for f in items) / len(items))


def mark_group(f: Features) -> int:
    """0, 1, 2: the teacher's mark in the low, middle or high third of the question's marks."""
    share = f.teacher_mark / f.max_marks
    return 0 if share < Decimal(1) / 3 else 1 if share < Decimal(2) / 3 else 2


def _balanced(pairs: Iterable[tuple[Features, Decimal]]) -> float:
    """Balanced mean of (answer, absolute difference) pairs."""
    groups: dict[int, list[Decimal]] = defaultdict(list)
    for f, diff in pairs:
        groups[mark_group(f)].append(diff)
    if not groups:
        return 0.0
    return sum(float(sum(d) / len(d)) for d in groups.values()) / len(groups)


def balanced_mae(items: Sequence[Features], policy: ScoringPolicy) -> float:
    """The mean of the mean absolute differences of the low, middle and high mark groups
    (groups with no answer left out)."""
    return _balanced((f, abs(f.mark(policy) - f.teacher_mark)) for f in items)


@dataclass(frozen=True, slots=True, kw_only=True)
class Baseline:
    """The best constant guess (a share of each question's marks, on its step): what a model
    must beat to be worth anything."""

    share: Decimal
    mae: float
    balanced: float


def best_constant(items: Sequence[Features], *, balanced: bool = False) -> Baseline:
    """The share of the marks that, given to every answer, comes closest to the teachers: by
    plain mean difference, or by the balanced one."""

    def guess(f: Features, share: Decimal) -> Decimal:
        return round_to_step(f.max_marks * share, f.mark_step)

    candidates = []
    for k in range(11):
        share = Decimal(k) / 10
        pairs = [(f, abs(guess(f, share) - f.teacher_mark)) for f in items]
        candidates.append(
            Baseline(
                share=share,
                mae=float(sum(d for _, d in pairs) / len(pairs)) if pairs else 0.0,
                balanced=_balanced(pairs),
            )
        )
    if balanced:
        return min(candidates, key=lambda b: (b.balanced, b.mae))
    return min(candidates, key=lambda b: (b.mae, b.balanced))


def fit_bands(items: Sequence[Features], base: ScoringPolicy | None = None) -> ScoringPolicy:
    """The (half, full) pair with the lowest balanced mean absolute difference (ties: the first
    in grid order, lowest edges first). Without semantic criteria the bands do not matter and
    ``base`` is kept."""
    base = base or ScoringPolicy()
    if not any(f.semantic for f in items):
        return base
    best: ScoringPolicy | None = None
    best_error = math.inf
    for half in _grid(0.0, 0.9, 0.025):
        for full in _grid(half + 0.025, 0.975, 0.025):
            candidate = replace(base, half=half, full=full)
            error = balanced_mae(items, candidate)
            if error < best_error - 1e-12:
                best, best_error = candidate, error
    return best or base


@dataclass(frozen=True, slots=True, kw_only=True)
class FlagFit:
    policy: ScoringPolicy
    recall: float
    """Share of disagreements (more than half a mark apart) that are flagged."""
    rate: float
    """Share of all answers flagged."""


def _percentile(values: Sequence[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


def fit_flags(
    items: Sequence[Features], policy: ScoringPolicy, *, max_flag_rate: float = 0.4
) -> FlagFit:
    """Relevance thresholds from the percentiles of genuine answers' relevance, then the
    margin catching the most disagreements while flagging at most ``max_flag_rate`` of the
    answers (ties: the fewest flags)."""
    relevances = [f.relevance for f in items if f.relevance is not None and f.words >= 1]
    if relevances:
        low = round(_percentile(relevances, 0.02), 3)
        policy = replace(
            policy,
            relevance_min=low,
            relevance_soft=max(low, round(_percentile(relevances, 0.10), 3)),
        )
    disagree = [f for f in items if abs(f.mark(policy) - f.teacher_mark) > HALF_MARK]
    best = FlagFit(policy=policy, recall=_recall(disagree, policy), rate=_rate(items, policy))
    for margin in _grid(0.0, 0.10, 0.01):
        candidate = replace(policy, margin=margin)
        rate = _rate(items, candidate)
        if rate > max_flag_rate:
            continue
        recall = _recall(disagree, candidate)
        if (recall, -rate) > (best.recall, -best.rate) or best.rate > max_flag_rate:
            best = FlagFit(policy=candidate, recall=recall, rate=rate)
    return best


def _rate(items: Sequence[Features], policy: ScoringPolicy) -> float:
    return sum(f.flagged(policy) for f in items) / len(items) if items else 0.0


def _recall(disagree: Sequence[Features], policy: ScoringPolicy) -> float:
    return sum(f.flagged(policy) for f in disagree) / len(disagree) if disagree else 1.0


def fit(items: Sequence[Features], *, max_flag_rate: float = 0.4) -> FlagFit:
    """Bands first, then the flag thresholds under those bands."""
    return fit_flags(items, fit_bands(items), max_flag_rate=max_flag_rate)


def cross_validated(items: Sequence[Features], folds: int = 5) -> tuple[float, float]:
    """(mean absolute difference, balanced) with the bands fitted without each group of
    questions and measured on it (grouped by question so a question's answers never sit on both
    sides)."""
    codes = sorted({f.question for f in items})
    if len(codes) < 2:
        policy = fit_bands(items)
        return mean_abs_diff(items, policy), balanced_mae(items, policy)
    groups = [codes[k::folds] for k in range(min(folds, len(codes)))]
    pairs: list[tuple[Features, Decimal]] = []
    for held in groups:
        held_set = set(held)
        policy = fit_bands([f for f in items if f.question not in held_set])
        pairs.extend(
            (f, abs(f.mark(policy) - f.teacher_mark)) for f in items if f.question in held_set
        )
    mae = float(sum(d for _, d in pairs) / len(pairs)) if pairs else 0.0
    return mae, _balanced(pairs)


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Rank correlation (average ranks for ties); 0 when either side is constant."""
    if len(xs) != len(ys) or len(xs) < 2:
        return 0.0
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return 0.0 if vx == 0 or vy == 0 else cov / (vx * vy)


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def semantic_signal(f: Features) -> float:
    """The answer's weighted mean semantic similarity (for rank correlation with the marks)."""
    total = sum((w for w, _ in f.semantic), Decimal(0))
    if total == 0:
        return 0.0
    return sum(float(w) * s for w, s in f.semantic) / float(total)


# --- the report -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class QuestionRow:
    code: str
    answers: int
    max_marks: Decimal
    mean_abs_diff: float
    within_half: float
    flagged: float


@dataclass(frozen=True, slots=True, kw_only=True)
class AccuracyReport:
    rows: tuple[QuestionRow, ...]
    overall: QuestionRow
    flag_recall: float
    balanced: float
    baseline: Baseline
    """The best constant guess by plain mean difference."""
    balanced_baseline: Baseline
    """The best constant guess by balanced mean difference."""


def accuracy(items: Sequence[Features], policy: ScoringPolicy) -> AccuracyReport:
    grouped: dict[str, list[Features]] = defaultdict(list)
    for f in items:
        grouped[f.question].append(f)

    def row(code: str, group: Sequence[Features]) -> QuestionRow:
        diffs = [abs(f.mark(policy) - f.teacher_mark) for f in group]
        return QuestionRow(
            code=code,
            answers=len(group),
            max_marks=max((f.max_marks for f in group), default=Decimal(0)),
            mean_abs_diff=float(sum(diffs, Decimal(0)) / len(diffs)) if diffs else 0.0,
            within_half=sum(d <= HALF_MARK for d in diffs) / len(diffs) if diffs else 0.0,
            flagged=_rate(group, policy),
        )

    disagree = [f for f in items if abs(f.mark(policy) - f.teacher_mark) > HALF_MARK]
    return AccuracyReport(
        rows=tuple(row(code, grouped[code]) for code in sorted(grouped)),
        overall=row("all", items),
        flag_recall=_recall(disagree, policy),
        balanced=balanced_mae(items, policy),
        baseline=best_constant(items),
        balanced_baseline=best_constant(items, balanced=True),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelResult:
    model: str
    answers: int
    spearman: float
    cv_balanced: float
    cv_mae: float
    fitted_balanced: float
    seconds_per_answer: float
    policy: ScoringPolicy = field(default_factory=ScoringPolicy)


def render_models(
    title: str,
    notes: Sequence[str],
    results: Sequence[ModelResult],
    baselines: tuple[Baseline, Baseline] | None = None,
) -> str:
    """``baselines``: the best constant guesses by plain and by balanced difference."""
    lines = [f"# {title}", "", *notes, ""]
    if baselines is not None:
        lines += [_baseline_line(*baselines), ""]
    lines += [
        "| Model | Answers | Spearman (similarity vs mark) | Balanced MAE, cross-validated | "
        "MAE, cross-validated | Balanced MAE, fitted | Bands (½, 1) | s / answer |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |",
    ]
    for r in sorted(results, key=lambda r: (r.cv_balanced, -r.spearman)):
        lines.append(
            f"| {r.model} | {r.answers} | {r.spearman:.3f} | {r.cv_balanced:.3f} | "
            f"{r.cv_mae:.3f} | {r.fitted_balanced:.3f} | {r.policy.half:.3f}, "
            f"{r.policy.full:.3f} | {r.seconds_per_answer:.3f} |"
        )
    return "\n".join(lines) + "\n"


def render_accuracy(
    title: str,
    notes: Sequence[str],
    report: AccuracyReport,
    policy: ScoringPolicy,
    *,
    flag_rate: float,
    fmt_mark: Callable[[Decimal], str] = lambda d: f"{d.normalize():f}",
) -> str:
    lines = [f"# {title}", "", *notes, ""]
    lines += [
        f"Bands: ½ credit from {policy.half:.3f}, full from {policy.full:.3f}; "
        f"borderline margin {policy.margin:.3f}; off-target below relevance "
        f"{policy.relevance_min:.3f} (names: below {policy.relevance_soft:.3f}).",
        "",
        f"Flags: {flag_rate:.0%} of answers flagged; {report.flag_recall:.0%} of the answers "
        "where AI and teacher differ by more than ½ mark are flagged.",
        "",
        f"Overall: mean difference {report.overall.mean_abs_diff:.3f}, balanced over the low, "
        f"middle and high thirds of the marks {report.balanced:.3f}. "
        + _baseline_line(report.baseline, report.balanced_baseline),
        "",
        "| Question | Answers | Max | Mean abs. diff. (marks) | Within ½ mark | Flagged |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in (*report.rows, report.overall):
        name = "**all**" if r.code == "all" else r.code
        lines.append(
            f"| {name} | {r.answers} | {fmt_mark(r.max_marks)} | {r.mean_abs_diff:.2f} | "
            f"{r.within_half:.0%} | {r.flagged:.0%} |"
        )
    return "\n".join(lines) + "\n"


def _baseline_line(plain: Baseline, balanced: Baseline) -> str:
    return (
        f"Best constant guesses (the same share of the marks for every answer): "
        f"{plain.share:.0%} by mean difference ({plain.mae:.3f}; balanced {plain.balanced:.3f}), "
        f"{balanced.share:.0%} by balanced difference ({balanced.balanced:.3f}; mean "
        f"{balanced.mae:.3f})."
    )
