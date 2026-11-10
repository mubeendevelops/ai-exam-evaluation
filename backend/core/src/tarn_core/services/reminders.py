"""The daily due-date sweep (P37, D227, D242): the ``notify.due`` job, one per college.

Today is the UTC date of the clock, as due dates are UTC dates (D209). Only open units are
looked at (a submitted or frozen unit is done). For each:

- **due soon** when it is due today or in the next three days: one notice per unit and due
  date (the key's day is the due date), and the "due in 3 days" email with it, sent only when
  the notice was new, so a rerun mails nothing;
- **overdue** when its due date has passed: one notice per unit and day while it stays open
  (no email: the bell reminds).

The notices' unique key (unit, kind, day) makes the sweep idempotent. The email holds the unit
code, paper, subject, year and due date: no student data."""

from dataclasses import dataclass
from datetime import UTC, date

from tarn_core.domain.content import Subject
from tarn_core.domain.notifications import Notification, NotificationKind
from tarn_core.domain.unit import Unit, UnitState
from tarn_core.errors import NotFoundError
from tarn_core.ids import CollegeId, NotificationId
from tarn_core.ports.identity import EmailMessage, Mailer
from tarn_core.ports.repositories import (
    ContentRepository,
    NotificationRepository,
    UnitRepository,
    UserRepository,
)
from tarn_core.services._support import Runtime
from tarn_core.services.unit_flow import paper_summary

DUE_SOON_DAYS = 3


@dataclass(frozen=True, slots=True)
class SweepResult:
    due_soon: int
    overdue: int
    emails: int


class ReminderSweep:
    def __init__(
        self,
        *,
        units: UnitRepository,
        users: UserRepository,
        content: ContentRepository,
        notifications: NotificationRepository,
        mailer: Mailer,
        runtime: Runtime,
    ) -> None:
        self._units = units
        self._users = users
        self._content = content
        self._notifications = notifications
        self._mailer = mailer
        self._rt = runtime

    def run(self, college_id: CollegeId) -> SweepResult:
        now = self._rt.clock.now()
        today = now.astimezone(UTC).date()
        due_soon = overdue = emails = 0
        for unit in sorted(
            self._units.list(college_id, state=UnitState.OPEN), key=lambda u: u.code
        ):
            days = (unit.due_date - today).days
            if 0 <= days <= DUE_SOON_DAYS:
                if self._add(unit, NotificationKind.DUE_SOON, unit.due_date):
                    due_soon += 1
                    if self._email(college_id, unit, days):
                        emails += 1
            elif days < 0 and self._add(unit, NotificationKind.OVERDUE, today):
                overdue += 1
        return SweepResult(due_soon=due_soon, overdue=overdue, emails=emails)

    def _add(self, unit: Unit, kind: NotificationKind, day: date) -> bool:
        return self._notifications.add(
            unit.college_id,
            Notification(
                id=self._rt.new_id(NotificationId),
                college_id=unit.college_id,
                user_id=unit.owner_id,
                kind=kind,
                unit_id=unit.id,
                day=day,
                created_at=self._rt.clock.now(),
                payload={"unit_code": unit.code, "due_date": unit.due_date.isoformat()},
            ),
        )

    def _email(self, college_id: CollegeId, unit: Unit, days: int) -> bool:
        owner = self._users.get(college_id, unit.owner_id)
        if not owner.active:
            return False
        try:
            paper = paper_summary(self._content, self._units, college_id, unit.id, unit.paper_id)
            paper_line = f"Paper: {paper.title} ({paper.exam_code})\n"
        except NotFoundError:
            paper_line = ""
        try:
            subject = self._content.get(college_id, Subject, unit.subject_id)
            subject_line = f"Subject: {subject.name} ({subject.code})\n"
        except NotFoundError:
            subject_line = ""
        when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
        self._mailer.send(
            EmailMessage(
                to=owner.email,
                subject=f"Tarn: unit {unit.code} is due {when}",
                text=(
                    f"Hello {owner.display_name},\n\n"
                    f"The unit {unit.code} is due {when}, on {unit.due_date.isoformat()}.\n\n"
                    f"{paper_line}{subject_line}"
                    f"Student year: {unit.year}\n\n"
                    "Sign in to Tarn AI Evaluation to finish marking it and submit it to your "
                    "admin."
                ),
            )
        )
        return True


__all__ = ["DUE_SOON_DAYS", "ReminderSweep", "SweepResult"]
