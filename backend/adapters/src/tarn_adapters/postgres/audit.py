"""PostgreSQL audit sink. Append only: the application role can INSERT into ``audit_events``
but not UPDATE or DELETE (rule 6).

A booklet deletion event also writes the content-free deletion record (who, when, which
booklet) and redacts the booklet's earlier events: actor, time and action stay, before and
after values are cleared by the database function ``tarn_redact_booklet_audit`` (D14, D32)."""

from sqlalchemy import Connection, insert, select, text

from tarn_adapters.postgres import metadata as m
from tarn_adapters.postgres.repositories import writing
from tarn_core.domain.audit import AuditAction, AuditEvent


class PgAuditSink:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def append(self, event: AuditEvent) -> None:
        with writing(self._conn, f"audit event {event.id}"):
            self._conn.execute(
                insert(m.audit_events).values(
                    id=event.id,
                    college_id=event.college_id,
                    actor_id=event.actor_id,
                    at=event.at,
                    action=event.action.value,
                    booklet_id=event.booklet_id,
                    answer_id=event.answer_id,
                    before=event.before,
                    after=event.after,
                )
            )
            if event.action is AuditAction.BOOKLET_DELETED:
                self._conn.execute(
                    insert(m.deletion_records).values(
                        id=event.id,
                        college_id=event.college_id,
                        booklet_id=event.booklet_id,
                        actor_id=event.actor_id,
                        at=event.at,
                    )
                )
                self._conn.execute(
                    select(text("tarn_redact_booklet_audit(:booklet)")),
                    {"booklet": event.booklet_id},
                )
