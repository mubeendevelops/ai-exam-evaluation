"""The question paper PDF (P34, D224): one paper version as a ``PaperDocument`` and its PDF.

Questions are linked by id (D30), so the paper shows the latest version of each one's text.
Only question text and marks are read; keys, rubrics and reference answers never are."""

from decimal import Decimal

from tarn_core.domain.blueprint import (
    AnyN,
    BlueprintKind,
    ExamBlueprint,
    OrGroup,
    QuestionSlot,
    Section,
)
from tarn_core.domain.content import Question, Subject
from tarn_core.domain.paper_sheet import (
    PaperChoice,
    PaperDocument,
    PaperItem,
    PaperPart,
    PaperQuestion,
    PaperSection,
)
from tarn_core.errors import NotFoundError
from tarn_core.ids import BlueprintId, CollegeId, QuestionId
from tarn_core.ports.rendering import PaperRenderer
from tarn_core.ports.repositories import CollegeRepository, ContentRepository
from tarn_core.services.access import Reach


def _marks(value: Decimal) -> str:
    return format(value.normalize(), "f")


def rule_text(section: Section) -> str:
    if isinstance(section.rule, AnyN):
        return f"Answer any {section.rule.n} of {len(section.items)}."
    return "Answer all questions."


def negative_text(section: Section) -> str:
    amounts = {s.negative_marks for s in section.slots() if s.negative_marks}
    if not amounts:
        return ""
    if len(amounts) == 1:
        return f"A wrong answer loses {_marks(amounts.pop())} marks."
    return "Wrong answers lose marks."


class PaperSheets:
    def __init__(
        self,
        *,
        content: ContentRepository,
        colleges: CollegeRepository,
        renderer: PaperRenderer,
    ) -> None:
        self._content = content
        self._colleges = colleges
        self._renderer = renderer

    def document(
        self, college_id: CollegeId, blueprint_id: BlueprintId, version: int, *, reach: Reach
    ) -> PaperDocument:
        """Another college's, another subject's or a missing version: ``NotFoundError``. A
        schema is "not found" too: it is not a paper."""
        paper = self._content.get(college_id, ExamBlueprint, blueprint_id, version)
        reach.check(paper.subject_id, f"paper {blueprint_id}")
        if paper.kind is not BlueprintKind.PAPER:
            raise NotFoundError(f"paper {blueprint_id}")
        texts: dict[QuestionId, str | None] = {}

        def text(question_id: QuestionId | None) -> str | None:
            if question_id is None:
                return None
            if question_id not in texts:
                try:
                    texts[question_id] = self._content.get(college_id, Question, question_id).text
                except NotFoundError:
                    texts[question_id] = None
            return texts[question_id]

        def question(slot: QuestionSlot) -> PaperQuestion:
            return PaperQuestion(
                number=slot.label,
                text=None if slot.parts else text(slot.question_id),
                marks=slot.marks,
                parts=tuple(
                    PaperPart(label=p.label, text=text(p.question_id), marks=p.marks)
                    for p in slot.parts
                ),
            )

        def item(entry: QuestionSlot | OrGroup) -> PaperItem:
            if isinstance(entry, OrGroup):
                return PaperChoice(alternatives=tuple(question(a) for a in entry.alternatives))
            return question(entry)

        try:
            subject_name = self._content.get(college_id, Subject, paper.subject_id).name
        except NotFoundError:
            subject_name = ""
        return PaperDocument(
            college_name=self._colleges.get(college_id).name,
            title=paper.title,
            exam_code=paper.exam_code,
            course_code=paper.course_code,
            subject_name=subject_name,
            duration_minutes=paper.duration_minutes,
            total_marks=paper.total_marks,
            version=paper.meta.version,
            sections=tuple(
                PaperSection(
                    label=s.label,
                    title=s.title,
                    rule=rule_text(s),
                    negative_note=negative_text(s),
                    items=tuple(item(i) for i in s.items),
                )
                for s in paper.sections
            ),
            unlinked=len(paper.unlinked_leaves()),
        )

    def pdf(
        self, college_id: CollegeId, blueprint_id: BlueprintId, version: int, *, reach: Reach
    ) -> tuple[PaperDocument, bytes]:
        document = self.document(college_id, blueprint_id, version, reach=reach)
        return document, self._renderer.render(document)
