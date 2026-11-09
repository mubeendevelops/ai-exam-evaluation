"""The SME's student list (P32, D217) on one scenario, for every ``BookletRepository``: the
in-memory one and PostgreSQL's query must filter, page and tally alike.

``build`` gives one unit four booklets (and another unit of the same paper one more, which the
list never shows):

- Student 1 (``…0001``): approved, two answers attempted and approved, one not attempted.
- Student 2 (``…0002``): in review, two attempted, one approved.
- Student 3 (``…0003``): uploaded (processing), no answers yet.
- "Zara Quinn" (``ZQ26…0001``): needs a retake.

Test code that lives in the package so adapters can reuse it."""

from dataclasses import dataclass, replace

from tarn_core.domain.booklet import Answer, AnswerStatus, Booklet, BookletStatus
from tarn_core.domain.student_list import AnswerTally, RowStatus, UnitRowQuery
from tarn_core.domain.tenancy import Student
from tarn_core.ids import AnswerId, BookletId, CollegeId, SegmentId, StudentId, UnitId
from tarn_core.ports.repositories import BookletRepository
from tarn_core.testing.builders import (
    Backend,
    CollegeFixture,
    ci_shaped_blueprint,
    make_services,
    unit_for,
)


@dataclass
class Scenario:
    college: CollegeFixture
    unit: UnitId
    other_unit: UnitId
    booklets: dict[str, BookletId]
    """By the student's roll number."""


def build(mem: Backend, college: CollegeFixture) -> Scenario:
    cid = college.id
    paper = ci_shaped_blueprint(mem, college)
    unit = unit_for(mem, cid, paper.id, year="2024")
    other_unit = unit_for(mem, cid, paper.id, year="2025")
    zara = Student(id=StudentId(mem.ids.new()), college_id=cid, name="Zara Quinn", usn="ZQ26A0001")
    mem.students.save(cid, zara)
    services = make_services(mem)

    def booklet(student: Student, unit_id: UnitId, status: BookletStatus) -> Booklet:
        made = services.booklets.register(
            cid,
            college.teacher.id,
            student_id=student.id,
            unit_id=unit_id,
            file_sha256=f"{mem.ids.new().int:064x}"[-64:],
        ).booklet
        if status is not made.status:
            made = replace(made, status=status, version=made.version + 1)
            mem.booklets.save(cid, made)
        return made

    def answers(b: Booklet, *states: AnswerStatus | None) -> None:
        """One answer per state; None: not attempted (no segments)."""
        for n, state in enumerate(states, start=1):
            mem.booklets.save_answer(
                cid,
                Answer(
                    id=AnswerId(mem.ids.new()),
                    college_id=cid,
                    booklet_id=b.id,
                    slot_label=str(n),
                    segment_ids=() if state is None else (SegmentId(mem.ids.new()),),
                    status=state or AnswerStatus.SUGGESTED,
                ),
            )

    s1, s2, s3 = college.students
    done = booklet(s1, unit, BookletStatus.APPROVED)
    answers(done, AnswerStatus.APPROVED, AnswerStatus.APPROVED, None)
    review = booklet(s2, unit, BookletStatus.IN_REVIEW)
    answers(review, AnswerStatus.APPROVED, AnswerStatus.SKIPPED)
    waiting = booklet(s3, unit, BookletStatus.UPLOADED)
    retake = booklet(zara, unit, BookletStatus.NEEDS_RETAKE)
    elsewhere = booklet(s1, other_unit, BookletStatus.IN_REVIEW)
    answers(elsewhere, AnswerStatus.SUGGESTED)
    return Scenario(
        college=college,
        unit=unit,
        other_unit=other_unit,
        booklets={
            s1.usn: done.id,
            s2.usn: review.id,
            s3.usn: waiting.id,
            zara.usn: retake.id,
        },
    )


def check(booklets: BookletRepository, s: Scenario) -> None:
    cid = s.college.id
    s1, s2, s3 = (x.usn for x in s.college.students)

    def usns(query: UnitRowQuery, unit: UnitId | None = None) -> list[str]:
        rows = booklets.unit_rows(cid, unit or s.unit, query)
        by_id = {v: k for k, v in s.booklets.items()}
        return [by_id[b.id] for b in rows.booklets]

    everything = booklets.unit_rows(cid, s.unit, UnitRowQuery())
    assert everything.matching == 4
    assert usns(UnitRowQuery()) == [s1, s2, s3, "ZQ26A0001"]  # by roll number
    assert everything.tally(s.booklets[s1]) == AnswerTally(attempted=2, approved=2)
    assert everything.tally(s.booklets[s2]) == AnswerTally(attempted=2, approved=1)
    assert everything.tally(s.booklets[s3]) == AnswerTally()
    # Filters: a roll number from its start (spaces and case ignored), a name anywhere.
    assert usns(UnitRowQuery(text="zq26")) == ["ZQ26A0001"]
    spaced = f" {s1[:5].lower()} {s1[5:-1]} "  # e.g. " tst26 A000 "
    assert usns(UnitRowQuery(text=spaced)) == [s1, s2, s3]
    assert usns(UnitRowQuery(text="0001")) == []  # not from the start
    assert usns(UnitRowQuery(text="QUINN")) == ["ZQ26A0001"]
    assert usns(UnitRowQuery(text="student")) == [s1, s2, s3]
    assert usns(UnitRowQuery(text="%")) == []  # a wildcard is a character
    assert usns(UnitRowQuery(rows=frozenset({RowStatus.COMPLETED}))) == [s1]
    both = frozenset({RowStatus.PROCESSING, RowStatus.NEEDS_RETAKE})
    assert usns(UnitRowQuery(rows=both)) == [s3, "ZQ26A0001"]
    assert usns(UnitRowQuery(rows=frozenset({RowStatus.READY}))) == []
    assert usns(UnitRowQuery(text="student", rows=frozenset({RowStatus.IN_REVIEW}))) == [s2]
    # Pages: the count is of every match, whatever the page holds.
    page = booklets.unit_rows(cid, s.unit, UnitRowQuery(offset=1, limit=2))
    assert usns(UnitRowQuery(offset=1, limit=2)) == [s2, s3] and page.matching == 4
    assert set(page.tallies) == {s.booklets[s2], s.booklets[s3]}
    past = booklets.unit_rows(cid, s.unit, UnitRowQuery(offset=8, limit=2))
    assert list(past.booklets) == [] and past.matching == 4
    # The other unit holds only its own booklet.
    other = booklets.unit_rows(cid, s.other_unit, UnitRowQuery())
    assert other.matching == 1 and other.tally(other.booklets[0].id) == AnswerTally(attempted=1)


def check_isolated(booklets: BookletRepository, own: CollegeId, s: Scenario) -> None:
    """``booklets`` of college ``own`` asks for another college's unit: nothing."""
    rows = booklets.unit_rows(own, s.unit, UnitRowQuery())
    assert list(rows.booklets) == [] and rows.matching == 0
