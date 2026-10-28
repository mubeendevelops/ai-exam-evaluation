"""Subject entitlements (D160, D178): the admin grants an SME the subjects they may work on
and takes them back. Everything an SME sees is filtered on these (``services.access``)."""

from collections.abc import Iterable

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.content import Subject
from tarn_core.domain.personas import Capability
from tarn_core.domain.tenancy import Role, SubjectGrant
from tarn_core.domain.unit import UnitState
from tarn_core.errors import IllegalTransitionError, InvariantError, NotFoundError
from tarn_core.ids import CollegeId, SubjectId, UserId
from tarn_core.ports.repositories import (
    ContentRepository,
    EntitlementRepository,
    UnitRepository,
    UserRepository,
)
from tarn_core.services._support import Runtime
from tarn_core.services.access import require

LAST_SUBJECT = "An SME keeps at least one subject; grant another before removing this one."
OPEN_UNITS = "This SME has open units in the subject: move them to another SME as you remove it."


class EntitlementService:
    def __init__(
        self,
        *,
        entitlements: EntitlementRepository,
        users: UserRepository,
        content: ContentRepository,
        units: UnitRepository,
        runtime: Runtime,
    ) -> None:
        self._entitlements = entitlements
        self._users = users
        self._content = content
        self._units = units
        self._rt = runtime

    def subjects_of(
        self, college_id: CollegeId, actor_id: UserId, user_id: UserId
    ) -> frozenset[SubjectId]:
        self._admin(college_id, actor_id)
        self._sme(college_id, user_id)
        return self._entitlements.subjects_of(college_id, user_id)

    def check_grantable(self, college_id: CollegeId, subject_ids: Iterable[SubjectId]) -> None:
        """Every subject exists in the college and is not retired (D181)."""
        for subject_id in subject_ids:
            try:
                subject = self._content.get(college_id, Subject, subject_id)
            except NotFoundError:
                raise InvariantError("subject: no such subject in your college") from None
            if subject.retired:
                raise InvariantError(f"subject {subject.code}: retired subjects cannot be granted")

    def grant(
        self, college_id: CollegeId, actor_id: UserId, user_id: UserId, subject_id: SubjectId
    ) -> frozenset[SubjectId]:
        self._admin(college_id, actor_id)
        self._sme(college_id, user_id)
        self.check_grantable(college_id, [subject_id])
        if subject_id not in self._entitlements.subjects_of(college_id, user_id):
            self._entitlements.grant(
                college_id,
                SubjectGrant(
                    college_id=college_id,
                    user_id=user_id,
                    subject_id=subject_id,
                    granted_by=actor_id,
                    granted_at=self._rt.clock.now(),
                ),
            )
            self._rt.record(
                college_id,
                actor_id,
                AuditAction.SUBJECT_GRANTED,
                after={"user_id": str(user_id), "subject_id": str(subject_id)},
            )
        return self._entitlements.subjects_of(college_id, user_id)

    def check_revocable(
        self, college_id: CollegeId, actor_id: UserId, user_id: UserId, subject_id: SubjectId
    ) -> None:
        """The admin may take ``subject_id`` from the SME: held, and not their last one."""
        self._admin(college_id, actor_id)
        self._sme(college_id, user_id)
        held = self._entitlements.subjects_of(college_id, user_id)
        if subject_id not in held:
            raise NotFoundError(f"subject {subject_id} of user {user_id}")
        if held == {subject_id}:
            raise IllegalTransitionError(LAST_SUBJECT)

    def revoke(
        self, college_id: CollegeId, actor_id: UserId, user_id: UserId, subject_id: SubjectId
    ) -> frozenset[SubjectId]:
        """Refused while the SME owns an open unit in the subject (D163): the admin moves those
        units in the same action (``TransferService.revoke_subject``)."""
        self.check_revocable(college_id, actor_id, user_id, subject_id)
        if self._units.list(
            college_id, owner_id=user_id, subject_id=subject_id, state=UnitState.OPEN
        ):
            raise IllegalTransitionError(OPEN_UNITS)
        self._entitlements.revoke(college_id, user_id, subject_id)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.SUBJECT_REVOKED,
            before={"user_id": str(user_id), "subject_id": str(subject_id)},
        )
        return self._entitlements.subjects_of(college_id, user_id)

    def _admin(self, college_id: CollegeId, actor_id: UserId) -> None:
        require(self._users.get(college_id, actor_id), Capability.MANAGE_SMES)

    def _sme(self, college_id: CollegeId, user_id: UserId) -> None:
        user = self._users.get(college_id, user_id)
        if user.removed:
            raise NotFoundError(f"user {user_id}")
        if user.role is not Role.SME:
            raise InvariantError("only an SME has subjects")
