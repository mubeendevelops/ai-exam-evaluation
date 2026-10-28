"""What a signed-in user may see (P24, D160, D178): the persona's capabilities and, for an SME,
the subjects the admin granted and, for booklets, the units the SME owns (P26). Filtering
happens here, in the core, not only in the UI: content outside the user's subjects and booklets
outside their units look missing (``NotFoundError``, 404), like another college's rows."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import Booklet
from tarn_core.domain.personas import CAPABILITIES, Capability
from tarn_core.domain.tenancy import Role, User
from tarn_core.errors import NotFoundError, PermissionDeniedError
from tarn_core.ids import BookletId, CollegeId, SubjectId, UnitId, UserId
from tarn_core.ports.repositories import (
    BookletRepository,
    ContentRepository,
    EntitlementRepository,
    UnitRepository,
    UserRepository,
)


@dataclass(frozen=True, slots=True)
class Reach:
    """The subjects a user's work is limited to. ``subjects`` None = every subject of the
    college (the admin's progress view, and the machine's own steps). ``units``: the units
    whose booklets the user may see (P26: an SME sees the booklets of the units they own);
    None = every unit."""

    subjects: frozenset[SubjectId] | None
    units: frozenset[UnitId] | None = None

    def sees(self, subject_id: SubjectId) -> bool:
        return self.subjects is None or subject_id in self.subjects

    def sees_booklet(self, subject_id: SubjectId, unit_id: UnitId) -> bool:
        return self.sees(subject_id) and (self.units is None or unit_id in self.units)

    def check(self, subject_id: SubjectId, what: str = "item") -> None:
        """Out of reach looks missing: the caller learns nothing about the item (D178)."""
        if not self.sees(subject_id):
            raise NotFoundError(what)


WHOLE_COLLEGE = Reach(None)
"""For operator tools, the seed and the pipeline, which act for the college as a whole."""


def reach_of(
    user: User, entitlements: EntitlementRepository, units: UnitRepository | None = None
) -> Reach:
    """Without ``units`` an SME's reach holds no booklet (content-only callers need none)."""
    match user.role:
        case Role.SME:
            owned = (
                frozenset()
                if units is None
                else frozenset(u.id for u in units.list(user.college_id, owner_id=user.id))
            )
            return Reach(entitlements.subjects_of(user.college_id, user.id), owned)
        case Role.ADMIN:
            return WHOLE_COLLEGE
        case Role.GOVERNOR:
            return Reach(frozenset(), frozenset())


def require(user: User, capability: Capability) -> User:
    if not user.active or capability not in CAPABILITIES[user.role]:
        raise PermissionDeniedError("Your role cannot do this.")
    return user


def booklet_subject(
    content: ContentRepository, college_id: CollegeId, booklet: Booklet
) -> SubjectId:
    """A booklet's subject is the subject of the paper it is pinned to."""
    paper = content.get(college_id, ExamBlueprint, booklet.blueprint.id, booklet.blueprint.version)
    return paper.subject_id


class AccessService:
    def __init__(
        self,
        *,
        users: UserRepository,
        entitlements: EntitlementRepository,
        content: ContentRepository,
        booklets: BookletRepository,
        units: UnitRepository,
    ) -> None:
        self._users = users
        self._entitlements = entitlements
        self._content = content
        self._booklets = booklets
        self._units = units

    def reach(self, college_id: CollegeId, user_id: UserId) -> Reach:
        return reach_of(self._users.get(college_id, user_id), self._entitlements, self._units)

    def booklet(self, college_id: CollegeId, reach: Reach, booklet_id: BookletId) -> Booklet:
        """The booklet, if its paper's subject and its unit are in reach; otherwise not
        found."""
        booklet = self._booklets.get(college_id, booklet_id)
        subject = booklet_subject(self._content, college_id, booklet)
        if not reach.sees_booklet(subject, booklet.unit_id):
            raise NotFoundError(f"booklet {booklet_id}")
        return booklet

    def visible(
        self, college_id: CollegeId, reach: Reach, booklets: Iterable[Booklet]
    ) -> Sequence[Booklet]:
        if reach.subjects is None and reach.units is None:
            return list(booklets)
        papers: dict[tuple[object, int], SubjectId] = {}
        out = []
        for b in booklets:
            key = (b.blueprint.id, b.blueprint.version)
            if key not in papers:
                papers[key] = booklet_subject(self._content, college_id, b)
            if reach.sees_booklet(papers[key], b.unit_id):
                out.append(b)
        return out


__all__ = [
    "WHOLE_COLLEGE",
    "AccessService",
    "Reach",
    "booklet_subject",
    "reach_of",
    "require",
]
