"""A registered, verified and approved college on the in-memory adapters (P4 tests). Synthetic
names only."""

from dataclasses import dataclass

from tarn_core.domain.identity import TenantRecord
from tarn_core.ids import CollegeId, UserId
from tarn_core.services.auth import AccountService, AuthService
from tarn_core.services.registration import RegistrationService
from tarn_core.services.roster import RosterService
from tarn_core.testing import InMemory

ADMIN_PASSWORD = "correct horse battery staple"  # noqa: S105  (synthetic test data)
TEACHER_PASSWORD = "teacher's own long passphrase"  # noqa: S105


@dataclass
class AuthWorld:
    mem: InMemory

    @property
    def auth(self) -> AuthService:
        m = self.mem
        return AuthService(identity=m.identity, users=m.users, kit=m.auth_kit, runtime=m.runtime)

    @property
    def accounts(self) -> AccountService:
        m = self.mem
        return AccountService(identity=m.identity, users=m.users, kit=m.auth_kit, runtime=m.runtime)

    @property
    def registration(self) -> RegistrationService:
        m = self.mem
        return RegistrationService(
            identity=m.identity,
            users=m.users,
            colleges=m.colleges,
            kit=m.auth_kit,
            runtime=m.runtime,
        )

    @property
    def roster(self) -> RosterService:
        m = self.mem
        return RosterService(students=m.students, users=m.users, runtime=m.runtime)

    def register(
        self, institution_id: str, email: str, *, verify: bool = True, approve: bool = True
    ) -> tuple[CollegeId, UserId]:
        college_id = CollegeId(self.mem.ids.new())
        self.registration.register(
            college_id,
            institution_id=institution_id,
            admin_name="Dr. Test Admin",
            email=email,
            password=ADMIN_PASSWORD,
        )
        admin = next(u for u in self.mem.users.list(college_id))
        if verify:
            self.registration.verify_email(college_id, self.mem.mailer.last_token(email))
        if approve:
            self.registration.approve(college_id, operator="ops@tarn")
        return college_id, admin.id

    def invite(self, college_id: CollegeId, admin_id: UserId, email: str) -> UserId:
        user = self.accounts.invite_teacher(
            college_id, admin_id, display_name="Teacher T", email=email
        )
        self.auth.accept_invite(college_id, self.mem.mailer.last_token(email), TEACHER_PASSWORD)
        return user.id

    def tenant(self, college_id: CollegeId) -> TenantRecord:
        return self.mem.identity.get_tenant(college_id)
