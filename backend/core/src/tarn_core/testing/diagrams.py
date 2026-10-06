"""A small synthetic world for diagram tests (P14): one question with a reference flowchart
(start → read n → stop) and diagram criteria, a one-slot paper, a booklet, and answers whose
page holds a diagram region with its labels. All content is synthetic."""

from dataclasses import dataclass
from decimal import Decimal

from tarn_core.domain.blueprint import ExamBlueprint, QuestionSlot, Section
from tarn_core.domain.booklet import (
    Answer,
    Booklet,
    LineReading,
    Page,
    Region,
    RegionKind,
    Segment,
    SegmentSource,
    SegmentSpan,
)
from tarn_core.domain.common import Box, EngineRef, college_blob_key
from tarn_core.domain.content import (
    CriterionType,
    DiagramComponent,
    DiagramParams,
    ListItem,
    ListParams,
    Question,
    ReferenceDiagram,
    Subject,
)
from tarn_core.domain.diagram import (
    DetectedArrow,
    DetectedShape,
    DiagramDetection,
    DiagramKind,
    NodeShape,
    Point,
)
from tarn_core.ids import AnswerId, BlueprintId, PageId, RegionId, SegmentId, SubjectId
from tarn_core.services.question_bank import CriterionSpec
from tarn_core.testing.builders import Backend, CollegeFixture, add_college, make_services, meta

PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic reference diagram"
OCR = EngineRef(name="scripted", version="1")

# A flowchart: three boxes in a column joined by two arrows (page pixels, page 1240 x 1754).
SHAPES = (
    (NodeShape.TERMINAL, Box(x0=500, y0=100, x1=700, y1=160), "start"),
    (NodeShape.IO, Box(x0=480, y0=260, x1=720, y1=330), "read n"),
    (NodeShape.TERMINAL, Box(x0=500, y0=430, x1=700, y1=490), "stop"),
)
ARROWS = (
    (Point(x=600, y=162), Point(x=600, y=258)),
    (Point(x=600, y=332), Point(x=600, y=428)),
)
DIAGRAM_BOX = Box(x0=400, y0=60, x1=820, y1=540)


def flowchart_detection(
    *, reverse_second: bool = False, drop_last: bool = False, confidence: float = 0.9
) -> DiagramDetection:
    shapes = SHAPES[:-1] if drop_last else SHAPES
    arrows = []
    for k, (tail, head) in enumerate(ARROWS):
        if reverse_second and k == 1:
            tail, head = head, tail
        arrows.append(
            DetectedArrow(
                box=Box(x0=590, y0=min(tail.y, head.y), x1=610, y1=max(tail.y, head.y)),
                tail=tail,
                head=head,
                has_head=True,
                confidence=confidence,
            )
        )
    return DiagramDetection(
        width=1240,
        height=1754,
        shapes=tuple(DetectedShape(shape=s, box=b, confidence=confidence) for s, b, _ in shapes),
        arrows=tuple(arrows),
    )


@dataclass
class DiagramWorld[B: Backend]:
    mem: B
    college: CollegeFixture
    question: Question
    reference: ReferenceDiagram
    blueprint: ExamBlueprint
    booklet: Booklet

    def answer(self, labels: tuple[str, ...] = ("start", "read n", "stop")) -> Answer:
        """An answer to slot 1: one page with a line of text, a diagram region and the labels
        inside it (one per shape, in shape order)."""
        mem, booklet = self.mem, self.booklet
        college_id = booklet.college_id
        index = len(mem.booklets.pages(college_id, booklet.id))
        page = Page(
            id=PageId(mem.ids.new()),
            college_id=college_id,
            booklet_id=booklet.id,
            index=index,
            image=college_blob_key(college_id, "booklet", str(booklet.id), f"p{index}.png"),
            width=1240,
            height=1754,
            text_read=True,
            reading_order=index,
        )
        mem.booklets.save_page(college_id, page)
        mem.blobs.put(page.image, b"synthetic page", "image/png")
        regions: list[Region] = []
        line_box = Box(x0=40, y0=20, x1=1200, y1=55)
        regions.append(_line(mem, page, RegionKind.TEXT_LINE, line_box, "1. A flowchart:"))
        regions.append(
            Region(
                id=RegionId(mem.ids.new()),
                college_id=college_id,
                page_id=page.id,
                kind=RegionKind.DIAGRAM,
                box=DIAGRAM_BOX,
            )
        )
        for (_, box, _), text in zip(SHAPES, labels, strict=False):
            inner = Box(x0=box.x0 + 20, y0=box.y0 + 10, x1=box.x1 - 20, y1=box.y1 - 10)
            regions.append(_line(mem, page, RegionKind.LABEL, inner, text))
        for r in regions:
            mem.booklets.save_region(college_id, r)
        segment = Segment(
            id=SegmentId(mem.ids.new()),
            college_id=college_id,
            booklet_id=booklet.id,
            slot_label="1",
            spans=(SegmentSpan(page_id=page.id, box=Box(x0=0, y0=0, x1=1240, y1=800)),),
            source=SegmentSource.RULE,
            region_ids=tuple(r.id for r in regions),
        )
        mem.booklets.save_segment(college_id, segment)
        answer = Answer(
            id=AnswerId(mem.ids.new()),
            college_id=college_id,
            booklet_id=booklet.id,
            slot_label="1",
            segment_ids=(segment.id,),
        )
        mem.booklets.save_answer(college_id, answer)
        return answer


def _line(mem: Backend, page: Page, kind: RegionKind, box: Box, text: str) -> Region:
    return Region(
        id=RegionId(mem.ids.new()),
        college_id=page.college_id,
        page_id=page.id,
        kind=kind,
        box=box,
        readings=(LineReading(engine=OCR, text=text, box=box, confidence=0.9),),
        chosen=0,
        line_score=0.9,
    )


def diagram_world[B: Backend](
    mem: B,
    *,
    kind: DiagramKind = DiagramKind.FLOWCHART,
    components: tuple[DiagramComponent, ...] = (DiagramComponent.WHOLE,),
    college: CollegeFixture | None = None,
) -> DiagramWorld[B]:
    """``components``: one diagram criterion per component, 2 marks each, plus a 2-mark list
    criterion (so the question is worth 2 + 2·len(components)). ``college``: an existing one
    (a PostgreSQL session is bound to one college), else a new one."""
    college = college or add_college(mem, f"DIA{mem.ids.new().hex[:4]}")
    services = make_services(mem)
    subject = Subject(
        id=SubjectId(mem.ids.new()),
        meta=meta(college),
        code=f"SYN3{mem.ids.new().hex[:5]}",
        name="Synthetic Logic",
    )
    mem.content.save(subject)
    marks = Decimal(2 + 2 * len(components))
    question = services.bank.create_question(
        college.id,
        college.teacher.id,
        subject_id=subject.id,
        code=f"SYN-FC-{mem.ids.new().hex[:6]}",
        text="Draw a flowchart that reads n and stops.",
        max_marks=marks,
    )
    reference = services.bank.upload_reference_diagram(
        college.id,
        college.teacher.id,
        question.id,
        file_name="flowchart.png",
        data=PNG,
        declared_type="image/png",
        no_student_data=True,
        kind=kind,
    )
    specs = [
        CriterionSpec(
            label="Names the input",
            type=CriterionType.LIST,
            weight=Decimal(2),
            params=ListParams(items=(ListItem(term="flowchart"),), required_count=1),
        ),
        *(
            CriterionSpec(
                label=f"Diagram ({c.value})",
                type=CriterionType.DIAGRAM,
                weight=Decimal(2),
                params=DiagramParams(reference_diagram_id=reference.id, component=c),
            )
            for c in components
        ),
    ]
    services.bank.set_rubric(college.id, college.teacher.id, question.id, specs)
    blueprint = ExamBlueprint(
        id=BlueprintId(mem.ids.new()),
        meta=meta(college),
        subject_id=subject.id,
        title="Synthetic flowchart paper",
        total_marks=marks,
        sections=(
            Section(
                label="A",
                items=(QuestionSlot(label="1", marks=marks, question_id=question.id),),
            ),
        ),
    )
    mem.content.save(blueprint)
    booklet = services.booklets.register(
        college.id,
        college.teacher.id,
        student_id=college.students[0].id,
        blueprint_id=blueprint.id,
        file_sha256=f"{mem.ids.new().int:064x}"[-64:],
    ).booklet
    return DiagramWorld(
        mem=mem,
        college=college,
        question=question,
        reference=reference,
        blueprint=blueprint,
        booklet=booklet,
    )
