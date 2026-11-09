"""Evaluated booklets (P18): the stored PDF of any result sheet version. The list of evaluated
booklets went with the Evaluated tab (P32, D215): the SME's student list shows each booklet's
sheets ("View Final"). College data; every call takes ``college_id``."""

from tarn_core.domain.booklet import BookletStatus
from tarn_core.domain.review import ResultSheet
from tarn_core.errors import NotFoundError
from tarn_core.ids import BookletId, CollegeId
from tarn_core.ports.repositories import (
    BookletRepository,
    ContentRepository,
    ResultSheetRepository,
    StudentRepository,
    UnitRepository,
)
from tarn_core.ports.storage import BlobStore
from tarn_core.services.access import Reach


class EvaluatedBooklets:
    def __init__(
        self,
        *,
        booklets: BookletRepository,
        students: StudentRepository,
        content: ContentRepository,
        sheets: ResultSheetRepository,
        blobs: BlobStore,
        units: UnitRepository,
    ) -> None:
        self._units = units
        self._booklets = booklets
        self._students = students
        self._content = content
        self._sheets = sheets
        self._blobs = blobs

    def pdf(
        self, college_id: CollegeId, booklet_id: BookletId, version: int, *, reach: Reach
    ) -> tuple[ResultSheet, bytes]:
        """The stored PDF of one version. ``NotFoundError`` for a booklet of another college
        or out of reach, a version that was never issued, or a sheet issued without a PDF."""
        booklet = self._booklets.get(college_id, booklet_id)
        unit = self._units.get(college_id, booklet.unit_id)
        if not reach.sees_booklet(unit.subject_id, booklet.unit_id):
            raise NotFoundError(f"booklet {booklet_id}")
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
