"""Uploading a unit's booklets (P26, D162): several PDFs at once, one per student, each named
by the student's roll number (USN), for example ``1XX21CS001.pdf``.

Every file is checked before anything is stored; a file that fails is reported with its
reason and nothing is kept for it. The valid ones take the uploader's free queue slots (C30,
D61) in the order sent; the rest are reported "not queued, upload again later".

File names are data: only the last part of a name counts (no folders, no ``..``), it is never
used as a storage key, and it is never logged or audited (it carries a roll number)."""

import hashlib
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.booklet import BookletStatus
from tarn_core.domain.personas import Capability
from tarn_core.domain.tenancy import normalise_usn
from tarn_core.errors import IllegalTransitionError, InvariantError, UploadTooLargeError
from tarn_core.ids import BookletId, CollegeId, StudentId, UnitId, UserId
from tarn_core.ports.repositories import (
    BookletRepository,
    StudentRepository,
    UnitRepository,
    UserRepository,
)
from tarn_core.services._support import Runtime
from tarn_core.services.access import require
from tarn_core.services.booklets import BookletService
from tarn_core.services.uploads import UploadLimits, UploadService

_USN = re.compile(r"[A-Z0-9]{4,20}")
_PDF = b"%PDF-"
NAME_MAX = 255
REPLACEABLE = frozenset({BookletStatus.NEEDS_RETAKE, BookletStatus.FAILED})


class FileOutcome(StrEnum):
    QUEUED = "queued"
    REFUSED = "refused"
    NOT_QUEUED = "not_queued"


class FileProblem(StrEnum):
    """Why a file was not queued. The UI words each one."""

    BAD_NAME = "bad_name"
    NOT_PDF = "not_pdf"
    EMPTY = "empty"
    TOO_LARGE = "too_large"
    UNKNOWN_USN = "unknown_usn"
    USN_TWICE = "usn_twice"
    SAME_FILE_TWICE = "same_file_twice"
    ALREADY_IN_UNIT = "already_in_unit"
    UPLOADED_BEFORE = "uploaded_before"
    QUEUE_FULL = "queue_full"


PROBLEM_TEXT: dict[FileProblem, str] = {
    FileProblem.BAD_NAME: "Name the file by the student's roll number, for example 1XX21CS001.pdf.",
    FileProblem.NOT_PDF: "Not a PDF.",
    FileProblem.EMPTY: "The file is empty.",
    FileProblem.TOO_LARGE: "The file is too large.",
    FileProblem.UNKNOWN_USN: "No student with this roll number: add or correct it in Students.",
    FileProblem.USN_TWICE: "Two files for this roll number: upload one.",
    FileProblem.SAME_FILE_TWICE: "The same file was sent twice under different names.",
    FileProblem.ALREADY_IN_UNIT: "This student's booklet is in the unit already.",
    FileProblem.UPLOADED_BEFORE: "This file was uploaded before in your college.",
    FileProblem.QUEUE_FULL: "Not queued: the queue is full. Upload it again later.",
}


def file_stem(name: str) -> tuple[str, str]:
    """``(stem, extension)`` of the last part of ``name``: folders (``/`` or ``\\``) dropped,
    Unicode compatibility forms folded (NFKC, so full-width digits become ASCII), spaces
    around it trimmed. The extension is lower-cased with its dot (``.pdf``), or ``""``."""
    last = re.split(r"[/\\]", name)[-1]
    last = unicodedata.normalize("NFKC", last).strip()
    stem, dot, extension = last.rpartition(".")
    if not dot:
        return last, ""
    return stem.strip(), "." + extension.strip().lower()


def parse_usn(name: str) -> str | None:
    """The roll number a file is named by, normalised like the roster (upper case, no spaces),
    or None when the name is not ``<roll number>.pdf``."""
    if len(name) > NAME_MAX:
        return None
    stem, extension = file_stem(name)
    if extension != ".pdf":
        return None
    usn = normalise_usn(stem)
    return usn if _USN.fullmatch(usn) else None


@dataclass(frozen=True, slots=True, kw_only=True)
class NamedFile:
    name: str
    data: bytes


@dataclass(frozen=True, slots=True, kw_only=True)
class FileResult:
    name: str
    outcome: FileOutcome
    problem: FileProblem | None = None
    usn: str | None = None
    student_id: StudentId | None = None
    booklet_id: BookletId | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadReport:
    results: tuple[FileResult, ...]

    def count(self, outcome: FileOutcome) -> int:
        return sum(1 for r in self.results if r.outcome is outcome)


@dataclass(slots=True)
class _Candidate:
    index: int
    file: NamedFile
    usn: str | None = None
    sha256: str = ""
    student_id: StudentId | None = None
    problem: FileProblem | None = None


class UnitUploadService:
    def __init__(
        self,
        *,
        uploads: UploadService,
        booklet_service: BookletService,
        units: UnitRepository,
        booklets: BookletRepository,
        students: StudentRepository,
        users: UserRepository,
        runtime: Runtime,
        limits: UploadLimits | None = None,
    ) -> None:
        self._uploads = uploads
        self._booklet_service = booklet_service
        self._units = units
        self._booklets = booklets
        self._students = students
        self._users = users
        self._rt = runtime
        self._limits = limits or UploadLimits()

    def upload(
        self, college_id: CollegeId, actor_id: UserId, unit_id: UnitId, files: Sequence[NamedFile]
    ) -> UploadReport:
        """Check every file, queue the valid ones while there are free slots."""
        require(self._users.get(college_id, actor_id), Capability.UPLOAD_BOOKLETS)
        unit = self._units.get(college_id, unit_id)
        if not files:
            raise InvariantError("Choose the PDFs to upload.")
        if len(files) > self._limits.max_batch_files:
            raise UploadTooLargeError(f"At most {self._limits.max_batch_files} files per upload.")
        candidates = [_Candidate(index=i, file=f) for i, f in enumerate(files)]
        for c in candidates:
            self._check_alone(c)
        live = [c for c in candidates if c.problem is None]
        self._refuse_repeats(live, lambda c: c.usn, FileProblem.USN_TWICE)
        self._refuse_repeats(
            [c for c in live if c.problem is None], lambda c: c.sha256, FileProblem.SAME_FILE_TWICE
        )
        in_unit = {b.student_id for b in self._booklets.in_unit(college_id, unit.id)}
        for c in candidates:
            if c.problem is None:
                self._check_against_college(college_id, c, in_unit)
        free = max(0, self._limits.max_waiting - self._booklets.count_waiting(college_id, actor_id))
        results: list[FileResult] = []
        for c in candidates:
            if c.problem is not None:
                results.append(self._refused(c, c.problem))
                continue
            if free == 0:
                results.append(
                    FileResult(
                        name=c.file.name,
                        outcome=FileOutcome.NOT_QUEUED,
                        problem=FileProblem.QUEUE_FULL,
                        usn=c.usn,
                        student_id=c.student_id,
                    )
                )
                continue
            if c.student_id is None:  # every candidate left was matched to a student
                raise InvariantError("unmatched file")
            registration = self._uploads.upload(
                college_id,
                actor_id,
                student_id=c.student_id,
                unit_id=unit.id,
                files=[c.file.data],
            )
            free -= 1
            results.append(
                FileResult(
                    name=c.file.name,
                    outcome=FileOutcome.QUEUED,
                    usn=c.usn,
                    student_id=c.student_id,
                    booklet_id=registration.booklet.id,
                )
            )
        report = UploadReport(results=tuple(results))
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.UNIT_UPLOADED,
            after={
                "unit_id": str(unit.id),
                "queued": report.count(FileOutcome.QUEUED),
                "refused": report.count(FileOutcome.REFUSED),
                "not_queued": report.count(FileOutcome.NOT_QUEUED),
            },
        )
        return report

    def replace(
        self, college_id: CollegeId, actor_id: UserId, booklet_id: BookletId, data: bytes
    ) -> BookletId:
        """A retake: the booklet (waiting for a retake, or failed) is deleted, its
        content-free deletion record kept, and the new PDF queued for the same student in the
        same unit. Everything is checked before anything is deleted."""
        require(self._users.get(college_id, actor_id), Capability.UPLOAD_BOOKLETS)
        old = self._booklets.get(college_id, booklet_id)
        if old.status not in REPLACEABLE:
            raise IllegalTransitionError(
                "Only a booklet that needs a retake, or that failed, can be replaced."
            )
        candidate = _Candidate(index=0, file=NamedFile(name=".pdf", data=data))
        self._check_content(candidate)
        if candidate.problem is not None:
            raise InvariantError(PROBLEM_TEXT[candidate.problem])
        if any(b.id != old.id for b in self._booklets.find_by_hash(college_id, candidate.sha256)):
            raise InvariantError(PROBLEM_TEXT[FileProblem.UPLOADED_BEFORE])
        if self._booklets.count_waiting(college_id, actor_id) >= self._limits.max_waiting:
            raise InvariantError(PROBLEM_TEXT[FileProblem.QUEUE_FULL])
        self._booklet_service.delete(college_id, actor_id, old.id)
        registration = self._uploads.upload(
            college_id,
            actor_id,
            student_id=old.student_id,
            unit_id=old.unit_id,
            files=[data],
            allow_duplicate=True,
        )
        new_id = registration.booklet.id
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.BOOKLET_REPLACED,
            booklet_id=new_id,
            before={"booklet_id": str(old.id)},
            after={"booklet_id": str(new_id), "unit_id": str(old.unit_id)},
        )
        return new_id

    # --- checks --------------------------------------------------------------------------------

    def _check_alone(self, c: _Candidate) -> None:
        """What the file shows by itself: its name and its bytes."""
        if len(c.file.name) > NAME_MAX:
            c.problem = FileProblem.BAD_NAME
            return
        _, extension = file_stem(c.file.name)
        if extension != ".pdf":
            c.problem = FileProblem.NOT_PDF
            return
        c.usn = parse_usn(c.file.name)
        if c.usn is None:
            c.problem = FileProblem.BAD_NAME
            return
        self._check_content(c)

    def _check_content(self, c: _Candidate) -> None:
        data = c.file.data
        if not data:
            c.problem = FileProblem.EMPTY
        elif len(data) > self._limits.max_total_bytes:
            c.problem = FileProblem.TOO_LARGE
        elif data[: len(_PDF)] != _PDF:
            c.problem = FileProblem.NOT_PDF
        else:
            c.sha256 = hashlib.sha256(data).hexdigest()

    @staticmethod
    def _refuse_repeats(
        candidates: list[_Candidate], key: Callable[[_Candidate], object], problem: FileProblem
    ) -> None:
        """Every file sharing ``key`` with another is refused: which one is right is the
        admin's call."""
        counts = Counter(key(c) for c in candidates)
        for c in candidates:
            if counts[key(c)] > 1:
                c.problem = problem

    def _check_against_college(
        self, college_id: CollegeId, c: _Candidate, in_unit: set[StudentId]
    ) -> None:
        if c.usn is None:
            c.problem = FileProblem.BAD_NAME
            return
        student = self._students.find_by_usn(college_id, c.usn)
        if student is None:
            c.problem = FileProblem.UNKNOWN_USN
            return
        c.student_id = student.id
        if student.id in in_unit:
            c.problem = FileProblem.ALREADY_IN_UNIT
        elif self._booklets.find_by_hash(college_id, c.sha256):
            c.problem = FileProblem.UPLOADED_BEFORE

    @staticmethod
    def _refused(c: _Candidate, problem: FileProblem) -> FileResult:
        return FileResult(
            name=c.file.name,
            outcome=FileOutcome.REFUSED,
            problem=problem,
            usn=c.usn,
            student_id=c.student_id,
        )
