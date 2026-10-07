"""Evaluate one booklet from a ``PageSource`` to a result, with no API, database or web UI (R1,
R2): the same services the API and the worker use, driven by a caller that has plain files.

``BookletEvaluation`` takes the booklet's files from the page source, registers the booklet
and has a ``StageRunner`` process it (prepare, read, segment, diagrams, score), then reads the
outcome back as plain data: the AI's suggested marks per answer and criterion, the flags, the
blueprint's total, and a draft result sheet.

Nothing here approves anything. The result is the AI's suggestion: ``approved`` is always
false and the sheet says DRAFT; a teacher decides every mark in the review (rule 7). The JSON
holds marks, labels, flags and the scorers' short reasons, never the student's text or name."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import Booklet, BookletStatus, Page
from tarn_core.domain.common import JsonValue
from tarn_core.domain.content import RubricCriterion
from tarn_core.domain.review import ResultLine, ResultSheet
from tarn_core.domain.scoring import AnswerScore
from tarn_core.errors import NotFoundError
from tarn_core.ids import (
    BlueprintId,
    BookletId,
    CollegeId,
    ResultSheetId,
    StudentId,
    UserId,
)
from tarn_core.ports.rendering import SheetRenderer
from tarn_core.ports.repositories import BookletRepository, ContentRepository, ScoreRepository
from tarn_core.ports.storage import PageSource
from tarn_core.services._support import Runtime
from tarn_core.services.pipeline import PageDecisions
from tarn_core.services.totals import TotalsService
from tarn_core.services.uploads import UploadService
from tarn_core.services.workflow.sheets import SheetBuilder, paper_order

RESULT_FORMAT = "tarn-evaluation/1"


class StageRunner(Protocol):
    """Processes the booklet's queued jobs, in order, until none is left (the worker's loop for
    one booklet, on whatever queue the caller has)."""

    def run(self, college_id: CollegeId, booklet_id: BookletId) -> Sequence[str]:
        """Returns the class names of the errors that ended a stage (never their messages)."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class CriterionResult:
    label: str
    weight: Decimal
    credit: Decimal
    marks: Decimal
    scorer: str
    """Scorer name and version, as recorded on the score."""
    flags: tuple[str, ...] = ()
    reason: str = ""
    second_opinion_credit: Decimal | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AnswerResult:
    label: str
    """Leaf label of the question: ``"7"`` or ``"12.a"``."""
    max_marks: Decimal
    mark: Decimal | None
    """The AI's suggested mark; None where the key is guidance only (``mark_manually``)."""
    flags: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    relevance: float | None = None
    criteria: tuple[CriterionResult, ...] = ()
    content_versions: tuple[str, ...] = ()
    """Every content item version the score used, as ``kind:id:version`` (rule 11)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SlotResult:
    section: str
    label: str
    mark: Decimal | None
    counted: bool
    outcome: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RetakePage:
    page: int
    """Page number, from 1."""
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class EvaluationResult:
    booklet_id: BookletId
    status: str
    """The booklet's status: ``scored`` when the evaluation is complete."""
    failure_reason: str | None = None
    retake: tuple[RetakePage, ...] = ()
    exam: str = ""
    answers: tuple[AnswerResult, ...] = ()
    slots: tuple[SlotResult, ...] = ()
    total: Decimal | None = None
    max_marks: Decimal | None = None
    unmarked: tuple[str, ...] = ()
    """Labels of answers with no AI mark: the teacher marks them."""
    errors: tuple[str, ...] = field(default=())
    """Class names of the errors that ended a stage (never messages: they can echo text)."""

    @property
    def complete(self) -> bool:
        return self.status == BookletStatus.SCORED.value

    def as_json(self) -> dict[str, JsonValue]:
        """The result as a JSON document. Marks are strings, so they stay exact decimals."""
        return {
            "format": RESULT_FORMAT,
            "approved": False,
            "note": "AI suggestions only: a teacher approves every mark in the review.",
            "booklet_id": str(self.booklet_id),
            "status": self.status,
            "failure_reason": self.failure_reason,
            "errors": list(self.errors),
            "retake": [{"page": r.page, "reasons": list(r.reasons)} for r in self.retake],
            "exam": self.exam,
            "total": None if self.total is None else str(self.total),
            "max_marks": None if self.max_marks is None else str(self.max_marks),
            "unmarked": list(self.unmarked),
            "slots": [
                {
                    "section": s.section,
                    "slot": s.label,
                    "mark": None if s.mark is None else str(s.mark),
                    "counted": s.counted,
                    "outcome": s.outcome,
                }
                for s in self.slots
            ],
            "answers": [
                {
                    "label": a.label,
                    "max_marks": str(a.max_marks),
                    "mark": None if a.mark is None else str(a.mark),
                    "flags": list(a.flags),
                    "reasons": list(a.reasons),
                    "relevance": a.relevance,
                    "content_versions": list(a.content_versions),
                    "criteria": [
                        {
                            "label": c.label,
                            "weight": str(c.weight),
                            "credit": str(c.credit),
                            "marks": str(c.marks),
                            "scorer": c.scorer,
                            "flags": list(c.flags),
                            "reason": c.reason,
                            "second_opinion_credit": None
                            if c.second_opinion_credit is None
                            else str(c.second_opinion_credit),
                        }
                        for c in a.criteria
                    ],
                }
                for a in self.answers
            ],
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class Evaluated:
    result: EvaluationResult
    sheet_pdf: bytes | None
    """The draft result sheet, when the booklet was scored."""


class BookletEvaluation:
    def __init__(
        self,
        *,
        source: PageSource,
        uploads: UploadService,
        runner: StageRunner,
        decisions: PageDecisions,
        booklets: BookletRepository,
        scores: ScoreRepository,
        content: ContentRepository,
        totals: TotalsService,
        sheets: SheetBuilder,
        renderer: SheetRenderer,
        runtime: Runtime,
    ) -> None:
        self._source = source
        self._uploads = uploads
        self._runner = runner
        self._decisions = decisions
        self._booklets = booklets
        self._scores = scores
        self._content = content
        self._totals = totals
        self._sheets = sheets
        self._renderer = renderer
        self._rt = runtime

    def evaluate(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        *,
        student_id: StudentId,
        blueprint_id: BlueprintId,
        use_anyway: bool = False,
    ) -> Evaluated:
        """Register the source's booklet for the student and exam, process it, and report.

        A page the quality gate flags stops the run with the booklet in ``needs_retake`` and
        the pages named in the result; ``use_anyway`` goes on with them (the teacher's "use
        anyway", recorded in the audit log like any other)."""
        booklet_id = self._rt.new_id(BookletId)
        files = [
            p.data
            for p in sorted(self._source.pages(college_id, booklet_id), key=lambda p: p.index)
        ]
        self._uploads.upload(
            college_id,
            actor_id,
            student_id=student_id,
            blueprint_id=blueprint_id,
            files=files,
            allow_duplicate=True,
            booklet_id=booklet_id,
        )
        errors = list(self._runner.run(college_id, booklet_id))
        booklet = self._booklets.get(college_id, booklet_id)
        if use_anyway and booklet.status is BookletStatus.NEEDS_RETAKE:
            for page in self._booklets.pages(college_id, booklet_id):
                if page.needs_retake:
                    self._decisions.use_anyway(college_id, actor_id, booklet_id, page.index)
            errors += self._runner.run(college_id, booklet_id)
        return self.report(college_id, actor_id, booklet_id, errors=errors)

    def report(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        booklet_id: BookletId,
        *,
        errors: Sequence[str] = (),
    ) -> Evaluated:
        """What the booklet's stored scores say now, and its draft sheet when it is scored."""
        booklet = self._booklets.get(college_id, booklet_id)
        blueprint = self._content.get(
            ExamBlueprint, booklet.blueprint.id, booklet.blueprint.version
        )
        base = EvaluationResult(
            booklet_id=booklet_id,
            status=booklet.status.value,
            failure_reason=booklet.failure_reason,
            retake=self._retake(college_id, booklet),
            exam=blueprint.title,
            errors=tuple(errors),
        )
        if booklet.status is not BookletStatus.SCORED:
            return Evaluated(result=base, sheet_pdf=None)

        preview = self._totals.preview(college_id, booklet_id)
        slots = tuple(
            SlotResult(
                section=s.section_label,
                label=s.slot_label,
                mark=s.mark,
                counted=s.counted,
                outcome=s.outcome.value,
            )
            for s in preview.result.slots
        )
        answers = []
        for answer in sorted(
            self._booklets.answers(college_id, booklet_id),
            key=lambda a: paper_order(a.slot_label),
        ):
            if not answer.segment_ids:
                continue  # emptied by a segment edit: not attempted
            scores = self._scores.scores(college_id, answer.id)
            if not scores:
                continue
            _, _, max_marks = blueprint.leaf(answer.slot_label)
            answers.append(self._answer(answer.slot_label, max_marks, scores[-1]))
        unmarked = {
            a.slot_label
            for a in self._booklets.answers(college_id, booklet_id)
            if a.id in set(preview.unmarked)
        }
        result = EvaluationResult(
            booklet_id=booklet_id,
            status=booklet.status.value,
            exam=blueprint.title,
            answers=tuple(answers),
            slots=slots,
            total=preview.result.total,
            max_marks=preview.result.max_marks,
            unmarked=tuple(sorted(unmarked, key=paper_order)),
            errors=tuple(errors),
        )
        sheet = ResultSheet(
            id=self._rt.new_id(ResultSheetId),
            college_id=college_id,
            booklet_id=booklet_id,
            version=1,
            lines=tuple(
                ResultLine(
                    section_label=s.section,
                    slot_label=s.label,
                    mark=s.mark,
                    counted=s.counted,
                    reason=s.outcome,
                )
                for s in slots
            ),
            total=preview.result.total,
            max_marks=preview.result.max_marks,
            issued_by=actor_id,
            issued_at=self._rt.clock.now(),
        )
        pdf = self._renderer.render(self._sheets.document(sheet, draft=True))
        return Evaluated(result=result, sheet_pdf=pdf)

    # --- pieces --------------------------------------------------------------------------

    def _retake(self, college_id: CollegeId, booklet: Booklet) -> tuple[RetakePage, ...]:
        if booklet.status is not BookletStatus.NEEDS_RETAKE:
            return ()
        pages: Sequence[Page] = self._booklets.pages(college_id, booklet.id)
        return tuple(
            RetakePage(page=p.index + 1, reasons=tuple(r.value for r in p.retake_reasons))
            for p in pages
            if p.needs_retake
        )

    def _answer(self, label: str, max_marks: Decimal, score: AnswerScore) -> AnswerResult:
        criteria = []
        for c in score.criterion_scores:
            try:
                name = self._content.get(RubricCriterion, c.criterion.id, c.criterion.version).label
            except NotFoundError:
                name = "Criterion"
            criteria.append(
                CriterionResult(
                    label=name,
                    weight=c.weight,
                    credit=c.credit,
                    marks=c.marks,
                    scorer=f"{c.scorer.name} {c.scorer.version}",
                    flags=c.flags,
                    reason="" if c.reason is None else c.reason.summary,
                    second_opinion_credit=None
                    if c.second_opinion is None
                    else c.second_opinion.credit,
                )
            )
        return AnswerResult(
            label=label,
            max_marks=max_marks,
            mark=score.mark,
            flags=tuple(f.value for f in score.flags),
            reasons=score.reasons,
            relevance=score.relevance,
            criteria=tuple(criteria),
            content_versions=tuple(
                sorted(f"{r.kind.value}:{r.id}:{r.version}" for r in score.content_versions)
            ),
        )
