"""Booklet registration, lookup and teacher-triggered deletion. College data only."""

from collections.abc import Sequence
from dataclasses import dataclass

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import Booklet, SourceFile
from tarn_core.domain.common import BlobKey, college_blob_key
from tarn_core.errors import BookletLockedError, InvariantError
from tarn_core.ids import BlueprintId, BookletId, CollegeId, StudentId, UserId
from tarn_core.ports.repositories import (
    BookletRepository,
    ContentRepository,
    ResultSheetRepository,
    ScoreRepository,
    StudentRepository,
    UserRepository,
)
from tarn_core.ports.storage import BlobStore
from tarn_core.services._support import Runtime, ref_json


def booklet_folder(college_id: CollegeId, booklet_id: BookletId) -> BlobKey:
    """``college/{id}/booklet/{id}``: everything stored for one booklet lives under it."""
    return college_blob_key(college_id, "booklet", str(booklet_id))


@dataclass(frozen=True, slots=True, kw_only=True)
class Registration:
    booklet: Booklet
    duplicates: tuple[BookletId, ...]
    """Booklets of the same college with the same file hash: shown as a warning, not refused."""


class BookletService:
    def __init__(
        self,
        *,
        booklets: BookletRepository,
        students: StudentRepository,
        users: UserRepository,
        content: ContentRepository,
        scores: ScoreRepository,
        sheets: ResultSheetRepository,
        blobs: BlobStore,
        runtime: Runtime,
    ):
        self._booklets = booklets
        self._students = students
        self._users = users
        self._content = content
        self._scores = scores
        self._sheets = sheets
        self._blobs = blobs
        self._rt = runtime

    def register(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        *,
        student_id: StudentId,
        blueprint_id: BlueprintId,
        file_sha256: str,
        booklet_id: BookletId | None = None,
        sources: tuple[SourceFile, ...] = (),
    ) -> Registration:
        """Create a booklet for a student picked from this college's roster (D17), pinned to
        the blueprint's current version."""
        self._users.get(college_id, actor_id)
        self._students.get(college_id, student_id)
        blueprint = self._content.get(ExamBlueprint, blueprint_id)
        unlinked = blueprint.unlinked_leaves()
        if unlinked:
            raise InvariantError(
                f"blueprint {blueprint.title!r} has no question linked for "
                f"{', '.join(unlinked)}; link them before registering booklets"
            )
        duplicates = tuple(b.id for b in self._booklets.find_by_hash(college_id, file_sha256))
        booklet = Booklet(
            id=booklet_id or self._rt.new_id(BookletId),
            college_id=college_id,
            student_id=student_id,
            blueprint=blueprint.ref,
            file_sha256=file_sha256,
            uploaded_by=actor_id,
            uploaded_at=self._rt.clock.now(),
            sources=sources,
        )
        self._booklets.save(college_id, booklet)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.BOOKLET_REGISTERED,
            booklet_id=booklet.id,
            after={
                "student_id": str(student_id),
                "blueprint": ref_json(blueprint.ref),
                "duplicate_of": [str(d) for d in duplicates],
            },
        )
        return Registration(booklet=booklet, duplicates=duplicates)

    def get(self, college_id: CollegeId, booklet_id: BookletId) -> Booklet:
        return self._booklets.get(college_id, booklet_id)

    def list(self, college_id: CollegeId) -> Sequence[Booklet]:
        return self._booklets.list(college_id)

    def delete(self, college_id: CollegeId, actor_id: UserId, booklet_id: BookletId) -> None:
        """Remove images, text and marks; keep a content-free deletion record (D14). Refused
        while another teacher has the booklet open."""
        self._users.get(college_id, actor_id)
        lock = self._booklets.lock(college_id, booklet_id)
        if lock is not None and lock.holder != actor_id and lock.live(self._rt.clock.now()):
            raise BookletLockedError(
                "another teacher has this booklet open",
                holder=lock.holder,
                expires_at=lock.expires_at,
            )
        answer_ids = [a.id for a in self._booklets.answers(college_id, booklet_id)]
        for page in self._booklets.pages(college_id, booklet_id):
            self._blobs.delete(page.image)
            if page.original is not None:
                self._blobs.delete(page.original)
        booklet = self._booklets.get(college_id, booklet_id)
        for source in booklet.sources:
            self._blobs.delete(source.key)
        # Anything else stored for the booklet (cleaned pages, later stages' crops).
        self._blobs.delete_prefix(booklet_folder(college_id, booklet_id))
        self._scores.delete_for_answers(college_id, answer_ids)
        self._sheets.delete_for_booklet(college_id, booklet_id)
        self._booklets.delete(college_id, booklet_id)
        self._rt.record(college_id, actor_id, AuditAction.BOOKLET_DELETED, booklet_id=booklet_id)
