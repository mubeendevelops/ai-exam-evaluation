"""Helpers shared by the services."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from tarn_core.domain.audit import AuditAction, AuditEvent
from tarn_core.domain.common import ContentRef, JsonValue
from tarn_core.ids import AnswerId, AuditEventId, BookletId, CollegeId, UserId
from tarn_core.ports.runtime import AuditSink, Clock, IdGenerator


@dataclass(frozen=True, slots=True)
class Runtime:
    """Clock, ids and audit sink: what every writing service needs."""

    clock: Clock
    ids: IdGenerator
    audit: AuditSink

    def new_id[T: UUID](self, kind: Callable[[UUID], T]) -> T:
        return kind(self.ids.new())

    def record(
        self,
        college_id: CollegeId,
        actor_id: UserId | None,
        action: AuditAction,
        *,
        booklet_id: BookletId | None = None,
        answer_id: AnswerId | None = None,
        before: JsonValue = None,
        after: JsonValue = None,
    ) -> None:
        self.audit.append(
            AuditEvent(
                id=self.new_id(AuditEventId),
                college_id=college_id,
                actor_id=actor_id,
                at=self.clock.now(),
                action=action,
                booklet_id=booklet_id,
                answer_id=answer_id,
                before=before,
                after=after,
            )
        )


def ref_json(ref: ContentRef) -> JsonValue:
    return {"kind": ref.kind.value, "id": str(ref.id), "version": ref.version}
