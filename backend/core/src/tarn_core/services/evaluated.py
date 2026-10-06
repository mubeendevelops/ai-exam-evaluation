"""The evaluated booklets database (P18): every approved booklet of the college with its
student, exam and result sheet versions, searchable by exam, student, USN and status, and the
stored PDF of any version. College data; every call takes ``college_id``."""

from dataclasses import dataclass

from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import APPROVED_STATUSES, Booklet, BookletStatus
from tarn_core.domain.review import ResultSheet
from tarn_core.domain.tenancy import Student, normalise_usn
from tarn_core.errors import InvariantError, NotFoundError
from tarn_core.ids import BookletId, CollegeId
from tarn_core.ports.repositories import (
    BookletRepository,
    ContentRepository,
    ResultSheetRepository,
    StudentRepository,
)
from tarn_core.ports.storage import BlobStore


@dataclass(frozen=True, slots=True, kw_only=True)
class EvaluatedBooklet:
    booklet: Booklet
    student: Student
    exam: str
    course_code: str
    sheets: tuple[ResultSheet, ...]
    """Every version, oldest first; never empty."""

    @property
    def latest(self) -> ResultSheet:
        return self.sheets[-1]


@dataclass(frozen=True, slots=True, kw_only=True)
class EvaluatedPage:
    items: tuple[EvaluatedBooklet, ...]
    total: int


class EvaluatedBooklets:
    def __init__(
        self,
        *,
        booklets: BookletRepository,
        students: StudentRepository,
        content: ContentRepository,
        sheets: ResultSheetRepository,
        blobs: BlobStore,
    ) -> None:
        self._booklets = booklets
        self._students = students
        self._content = content
        self._sheets = sheets
        self._blobs = blobs

    def search(
        self,
        college_id: CollegeId,
        *,
        exam: str = "",
        student: str = "",
        usn: str = "",
        status: BookletStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> EvaluatedPage:
        """Case-insensitive "contains" on the exam title or course code, the student's name and
        the USN; ``status`` is one of the approved states. Newest result sheet first."""
        if status is not None and status not in APPROVED_STATUSES:
            raise InvariantError(f"an evaluated booklet is {_names(APPROVED_STATUSES)}")
        exam_q, student_q, usn_q = exam.strip().lower(), student.strip().lower(), normalise_usn(usn)
        students = {s.id: s for s in self._students.list(college_id)}
        blueprints: dict[tuple[object, int], ExamBlueprint] = {}
        found: list[EvaluatedBooklet] = []
        for booklet in self._booklets.list(college_id):
            if booklet.status not in APPROVED_STATUSES or status not in (None, booklet.status):
                continue
            owner = students.get(booklet.student_id)
            if owner is None:
                continue
            if student_q and student_q not in owner.name.lower():
                continue
            if usn_q and usn_q not in owner.usn:
                continue
            ref = booklet.blueprint
            blueprint = blueprints.get((ref.id, ref.version))
            if blueprint is None:
                blueprint = blueprints[(ref.id, ref.version)] = self._content.get(
                    ExamBlueprint, ref.id, ref.version
                )
            if exam_q and exam_q not in f"{blueprint.title} {blueprint.course_code}".lower():
                continue
            versions = tuple(self._sheets.versions(college_id, booklet.id))
            if not versions:
                continue
            found.append(
                EvaluatedBooklet(
                    booklet=booklet,
                    student=owner,
                    exam=blueprint.title,
                    course_code=blueprint.course_code,
                    sheets=versions,
                )
            )
        found.sort(key=lambda e: (e.latest.issued_at, str(e.booklet.id)), reverse=True)
        return EvaluatedPage(items=tuple(found[offset : offset + limit]), total=len(found))

    def pdf(
        self, college_id: CollegeId, booklet_id: BookletId, version: int
    ) -> tuple[ResultSheet, bytes]:
        """The stored PDF of one version. ``NotFoundError`` for a booklet of another college,
        a version that was never issued, or a sheet issued without a PDF."""
        self._booklets.get(college_id, booklet_id)
        sheet = next(
            (s for s in self._sheets.versions(college_id, booklet_id) if s.version == version),
            None,
        )
        if sheet is None:
            raise NotFoundError(f"result sheet v{version}")
        if sheet.pdf is None:
            raise NotFoundError(f"no PDF was stored for result sheet v{version}")
        return sheet, self._blobs.get(sheet.pdf)


def _names(statuses: frozenset[BookletStatus]) -> str:
    return " or ".join(sorted(s.value for s in statuses))
