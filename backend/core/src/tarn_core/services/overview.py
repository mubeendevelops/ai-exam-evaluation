"""The admin's dashboard, SME progress report and health box (P29, D163, D167).

Figures only: counts of SMEs, units and booklets by stage, who is overdue, who touched what
last. No marks (D167), no speed metric, no booklet or student data. The repository computes
them in the store; this service checks the capability and puts names to ids. College data:
every call takes the college first."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.content import Subject
from tarn_core.domain.overview import (
    ACTIVITY_LIMIT,
    ActivityItem,
    QueueDepth,
    SmeCounts,
    SmeProgress,
    StageCounts,
    UnitCounts,
    UnitProgress,
)
from tarn_core.domain.personas import Capability
from tarn_core.domain.tenancy import Role
from tarn_core.ids import CollegeId, UserId
from tarn_core.ports.repositories import (
    ContentRepository,
    OverviewRepository,
    UserRepository,
)
from tarn_core.ports.storage import BlobStore
from tarn_core.services._support import Runtime
from tarn_core.services.access import require


@dataclass(frozen=True, slots=True, kw_only=True)
class Dashboard:
    smes: SmeCounts
    units: UnitCounts
    booklets: StageCounts
    activity: Sequence[ActivityItem]


@dataclass(frozen=True, slots=True, kw_only=True)
class SystemFigures:
    """What the college can see of the system: its own jobs and what it stores. The API's and
    the worker's health come from the health endpoint (they are not the college's)."""

    queue: QueueDepth
    storage_bytes: int


@dataclass(frozen=True, slots=True, kw_only=True)
class UnitRow:
    progress: UnitProgress
    paper_title: str | None
    exam_code: str | None
    subject_code: str | None
    subject_name: str | None
    owner_name: str

    # The paper and subject are None when they were deleted after a freeze (D166).


@dataclass(frozen=True, slots=True, kw_only=True)
class ProgressReport:
    smes: Sequence[SmeProgress]
    units: Sequence[UnitRow]


class OverviewService:
    def __init__(
        self,
        *,
        overview: OverviewRepository,
        users: UserRepository,
        content: ContentRepository,
        blobs: BlobStore,
        runtime: Runtime,
    ) -> None:
        self._overview = overview
        self._users = users
        self._content = content
        self._blobs = blobs
        self._rt = runtime

    def _today(self) -> date:
        return self._rt.clock.now().date()

    def dashboard(self, college_id: CollegeId, actor_id: UserId) -> Dashboard:
        require(self._users.get(college_id, actor_id), Capability.VIEW_OVERVIEW)
        return Dashboard(
            smes=self._overview.sme_counts(college_id),
            units=self._overview.unit_counts(college_id, self._today()),
            booklets=self._overview.booklet_stages(college_id),
            activity=self._overview.activity(college_id, ACTIVITY_LIMIT),
        )

    def progress(self, college_id: CollegeId, actor_id: UserId) -> ProgressReport:
        """Every live SME and every unit, with names put to the ids."""
        require(self._users.get(college_id, actor_id), Capability.VIEW_OVERVIEW)
        today = self._today()
        papers = {p.id: p for p in self._content.latest(college_id, ExamBlueprint)}
        subjects = {s.id: s for s in self._content.latest(college_id, Subject)}
        owners = {u.id: u.display_name for u in self._users.list(college_id)}
        rows = []
        for unit in self._overview.unit_progress(college_id, today):
            paper = papers.get(unit.paper_id)
            subject = subjects.get(unit.subject_id)
            rows.append(
                UnitRow(
                    progress=unit,
                    paper_title=None if paper is None else paper.title,
                    exam_code=None if paper is None else paper.exam_code,
                    subject_code=None if subject is None else subject.code,
                    subject_name=None if subject is None else subject.name,
                    owner_name=owners.get(unit.owner_id, "Unknown"),
                )
            )
        return ProgressReport(smes=self._overview.sme_progress(college_id, today), units=rows)

    def system(self, college_id: CollegeId, actor_id: UserId) -> SystemFigures:
        require(self._users.get(college_id, actor_id), Capability.VIEW_OVERVIEW)
        return SystemFigures(
            queue=self._overview.queue_depth(college_id),
            storage_bytes=self._blobs.usage(college_id),
        )

    def idle_smes(self, college_id: CollegeId, actor_id: UserId) -> frozenset[UserId]:
        """The SMEs the directory marks *Idle* (D163): active, not removed, and owning no open
        unit."""
        require(self._users.get(college_id, actor_id), Capability.MANAGE_SMES)
        busy = self._overview.owners_of_open_units(college_id)
        return frozenset(
            u.id
            for u in self._users.list(college_id)
            if u.role is Role.SME and u.active and u.id not in busy
        )
