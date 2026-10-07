"""The result sheet PDF (P18, design.md "Workflow engine": "Result sheet v1 generated as PDF
and stored"; amended answer approved: "v2 issued, v1 kept").

``SheetBuilder`` gathers what the sheet shows (college, exam, course, student, each answer's
text excerpt and drawings, marks and criteria, remarks, the answers not counted, total,
version, amendment note) from the review as it stands when the sheet is issued. ``SheetPublisher``
draws it through the ``SheetRenderer`` port and stores it under the booklet's folder, so
deleting the booklet deletes every version. The PDF is rendered once, at issue: a later
amendment makes a new version and never rewrites an earlier one."""

from collections.abc import Mapping
from dataclasses import dataclass

from tarn_core.domain.blueprint import ExamBlueprint, leaves
from tarn_core.domain.booklet import Booklet, Page, Region, Segment
from tarn_core.domain.common import BlobKey, college_blob_key
from tarn_core.domain.content import Question, RubricCriterion, Subject
from tarn_core.domain.review import ResultLine, ResultSheet
from tarn_core.domain.scoring import AnswerScore
from tarn_core.domain.sheet import (
    SheetAnswer,
    SheetCriterion,
    SheetDiagram,
    SheetDocument,
)
from tarn_core.errors import NotFoundError
from tarn_core.ids import BookletId, CollegeId, PageId, QuestionId, RegionId, SegmentId
from tarn_core.ports.rendering import SheetRenderer
from tarn_core.ports.repositories import (
    BookletRepository,
    CollegeRepository,
    ContentRepository,
    ScoreRepository,
    StudentRepository,
    UserRepository,
)
from tarn_core.ports.storage import BlobStore
from tarn_core.services.scoring.assemble import segment_text

EXCERPT_CHARS = 1200
QUESTION_CHARS = 300
PDF_MEDIA_TYPE = "application/pdf"


def paper_order(label: str) -> tuple[tuple[int, int | str], ...]:
    """Paper order of leaf labels: "2" < "10" < "10.a"."""
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in label.split("."))


def sheet_key(college_id: CollegeId, booklet_id: BookletId, version: int) -> BlobKey:
    """``college/{id}/booklet/{id}/sheets/v{n}.pdf``: under the booklet's folder, which
    booklet deletion empties."""
    return college_blob_key(college_id, "booklet", str(booklet_id), "sheets", f"v{version}.pdf")


def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class SheetBuilder:
    def __init__(
        self,
        *,
        booklets: BookletRepository,
        scores: ScoreRepository,
        content: ContentRepository,
        colleges: CollegeRepository,
        students: StudentRepository,
        users: UserRepository,
        blobs: BlobStore,
    ) -> None:
        self._booklets = booklets
        self._scores = scores
        self._content = content
        self._colleges = colleges
        self._students = students
        self._users = users
        self._blobs = blobs

    def document(self, sheet: ResultSheet, *, draft: bool = False) -> SheetDocument:
        """What the sheet shows. A ``draft`` is the AI's suggestions as they stand (no review
        exists yet): marks, criteria and the question come from each answer's latest score, and
        the sheet is marked as unapproved."""
        college_id = sheet.college_id
        booklet = self._booklets.get(college_id, sheet.booklet_id)
        blueprint = self._content.get(
            ExamBlueprint, booklet.blueprint.id, booklet.blueprint.version
        )
        student = self._students.get(college_id, booklet.student_id)
        slot_of = {label: s.label for s in blueprint.slots() for label, _, _ in leaves(s)}
        line_of: Mapping[str, ResultLine] = {ln.slot_label: ln for ln in sheet.lines}
        regions = self._regions(booklet)
        pages = {p.id: p for p in self._booklets.pages(college_id, booklet.id)}
        segments = {s.id: s for s in self._booklets.segments(college_id, booklet.id)}
        images: dict[PageId, bytes | None] = {}

        answers: list[SheetAnswer] = []
        for answer in sorted(
            self._booklets.answers(college_id, booklet.id), key=lambda a: paper_order(a.slot_label)
        ):
            if not answer.segment_ids:
                continue  # emptied by a segment edit: not attempted
            slot, question_id, max_marks = blueprint.leaf(answer.slot_label)
            line = line_of.get(slot_of.get(answer.slot_label, slot.label))
            scores = self._scores.scores(college_id, answer.id)
            reviews = [] if draft else self._scores.reviews(college_id, answer.id)
            review = reviews[-1] if reviews else None
            score = next(
                (s for s in scores if review is not None and s.id == review.answer_score_id), None
            )
            suggestion = scores[-1] if scores else None
            if draft:
                score = suggestion
            own = sorted(
                (segments[i] for i in answer.segment_ids if i in segments), key=lambda s: s.position
            )
            answers.append(
                SheetAnswer(
                    label=answer.slot_label,
                    section_label=line.section_label if line else "",
                    question=self._question_text(score or suggestion, question_id),
                    max_marks=max_marks,
                    ai_mark=(
                        (None if suggestion is None else suggestion.mark)
                        if draft
                        else (None if review is None else review.ai_mark)
                    ),
                    teacher_mark=None if review is None else review.teacher_mark,
                    counted=line.counted if line else False,
                    outcome=line.reason if line else "",
                    excerpt=_cut(segment_text(own, regions).text, EXCERPT_CHARS),
                    diagrams=self._diagrams(college_id, booklet, own, regions, pages, images),
                    criteria=self._criteria(score),
                    tags=() if review is None else review.tags,
                    remarks="" if review is None else review.remarks,
                )
            )
        return SheetDocument(
            college_name=self._colleges.get(college_id).name,
            exam=blueprint.title,
            course=self._course(blueprint),
            student_name=student.name,
            usn=student.usn,
            version=sheet.version,
            total=sheet.total,
            max_marks=sheet.max_marks,
            issued_by=self._users.get(college_id, sheet.issued_by).display_name,
            generated_at=sheet.issued_at,
            lines=sheet.lines,
            answers=tuple(answers),
            amendment_note=sheet.note,
            draft=draft,
        )

    # --- pieces --------------------------------------------------------------------------

    def _regions(self, booklet: Booklet) -> dict[RegionId, Region]:
        regions: dict[RegionId, Region] = {}
        for page in self._booklets.pages(booklet.college_id, booklet.id):
            for region in self._booklets.regions(booklet.college_id, page.id):
                regions[region.id] = region
        return regions

    def _course(self, blueprint: ExamBlueprint) -> str:
        try:
            subject = self._content.get(Subject, blueprint.subject_id)
        except NotFoundError:
            return blueprint.course_code
        code = blueprint.course_code or subject.code
        return f"{code} · {subject.name}"

    def _question_text(self, score: AnswerScore | None, fallback: QuestionId | None) -> str:
        try:
            if score is not None:
                question = self._content.get(Question, score.question.id, score.question.version)
            elif fallback is not None:
                question = self._content.get(Question, fallback)
            else:
                return ""
        except NotFoundError:
            return ""
        return _cut(question.text, QUESTION_CHARS)

    def _criteria(self, score: AnswerScore | None) -> tuple[SheetCriterion, ...]:
        if score is None:
            return ()
        out: list[SheetCriterion] = []
        for c in score.criterion_scores:
            try:
                label = self._content.get(
                    RubricCriterion, c.criterion.id, c.criterion.version
                ).label
            except NotFoundError:
                label = "Criterion"
            out.append(
                SheetCriterion(
                    label=label,
                    weight=c.weight,
                    credit=c.credit,
                    marks=c.marks,
                    reason="" if c.reason is None else c.reason.summary,
                )
            )
        return tuple(out)

    def _diagrams(
        self,
        college_id: CollegeId,
        booklet: Booklet,
        segments: list[Segment],
        regions: Mapping[RegionId, Region],
        pages: Mapping[PageId, Page],
        images: dict[PageId, bytes | None],
    ) -> tuple[SheetDiagram, ...]:
        wanted: set[SegmentId] = {s.id for s in segments}
        out: list[SheetDiagram] = []
        for d in self._booklets.diagrams(college_id, booklet.id):
            if d.segment_id not in wanted:
                continue
            region = regions.get(d.region_id) if d.region_id is not None else None
            page = None if region is None else pages.get(region.page_id)
            image: bytes | None = None
            if page is not None:
                if page.id not in images:
                    try:
                        images[page.id] = self._blobs.get(page.image)
                    except NotFoundError:
                        images[page.id] = None
                image = images[page.id]
            out.append(
                SheetDiagram(
                    image=image,
                    box=d.box,
                    caption=f"Drawing {len(out) + 1} ({d.kind.value.replace('_', ' ')})",
                )
            )
        return tuple(out)


@dataclass(frozen=True, slots=True)
class SheetPublisher:
    """Renders a sheet and stores the PDF; returns where it went."""

    builder: SheetBuilder
    renderer: SheetRenderer
    blobs: BlobStore

    def publish(self, sheet: ResultSheet) -> BlobKey:
        pdf = self.renderer.render(self.builder.document(sheet))
        key = sheet_key(sheet.college_id, sheet.booklet_id, sheet.version)
        self.blobs.put(key, pdf, PDF_MEDIA_TYPE)
        return key


__all__ = [
    "EXCERPT_CHARS",
    "PDF_MEDIA_TYPE",
    "SheetBuilder",
    "SheetPublisher",
    "paper_order",
    "sheet_key",
]
