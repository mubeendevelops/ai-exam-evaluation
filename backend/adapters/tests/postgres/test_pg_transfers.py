"""Transfers and the delegation inbox on PostgreSQL (P27, migration 0015): the service in one
transaction, the history and requests round trip, one pending request per unit, the grants
(transfers insert only, requests never deleted), ``users.removed_at`` and the other college."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg import errors

from tarn_adapters.postgres.testing import Opener, TestDatabase, new_college
from tarn_core.domain.review import BookletLock
from tarn_core.domain.tenancy import Role, User
from tarn_core.domain.unit import DelegationRequest, RequestState
from tarn_core.errors import AlreadyExistsError, NotFoundError, StaleWriteError
from tarn_core.ids import DelegationRequestId, UserId
from tarn_core.testing import MemoryMailer
from tarn_core.testing.builders import ci_shaped_blueprint, make_services, unit_for

pytestmark = pytest.mark.integration


def test_a_transfer_on_postgres(session: Opener, test_database: TestDatabase) -> None:
    a = new_college(session, "TRA")
    b = new_college(session, "TRB")
    mail = MemoryMailer()
    with session(a.id) as s:
        svc = make_services(s, mailer=mail)
        paper = ci_shaped_blueprint(s, a)
        unit_id = unit_for(s, a.id, paper.id)
        second = User(
            id=UserId(uuid4()),
            college_id=a.id,
            display_name="Second SME",
            email="second@tra.example",
            role=Role.SME,
        )
        s.users.save(a.id, second)
        svc.entitlements.grant(a.id, a.admin.id, second.id, paper.subject_id)
        booklet = svc.booklets.register(
            a.id,
            a.teacher.id,
            student_id=a.students[0].id,
            unit_id=unit_id,
            file_sha256="c" * 64,
        ).booklet
        s.booklets.save_lock(
            a.id,
            BookletLock(
                college_id=a.id,
                booklet_id=booklet.id,
                holder=a.teacher.id,
                acquired_at=s.clock.now(),
                expires_at=s.clock.now() + timedelta(minutes=15),
            ),
        )
        asked = svc.delegation.request(a.id, a.teacher.id, unit_id, reason="Too many")
        with pytest.raises(AlreadyExistsError):  # the partial unique index, through the repo
            s.units.save_request(a.id, replace(asked, id=DelegationRequestId(uuid4())))
    with session(a.id) as s:
        svc = make_services(s, mailer=mail)
        moved = svc.transfers.transfer(
            a.id, a.admin.id, unit_id, owner_id=second.id, reason="Leave", expected_version=1
        )
        assert moved.owner_id == second.id and moved.version == 2
    with session(a.id) as s:
        assert s.units.get(a.id, unit_id).owner_id == second.id
        assert s.booklets.lock(a.id, booklet.id) is None
        done = s.units.get_request(a.id, asked.id)
        assert (done.state, done.closed_by) == (RequestState.DONE, a.admin.id)
        [history] = s.units.transfers(a.id, unit_id)
        assert (history.from_owner, history.to_owner, history.reason, history.request_id) == (
            a.teacher.id,
            second.id,
            "Leave",
            asked.id,
        )
        assert [r.id for r in s.units.requests(a.id, requested_by=a.teacher.id)] == [asked.id]
        assert s.units.requests(a.id, state=RequestState.PENDING) == []
        with pytest.raises(StaleWriteError):  # a closed request is not reopened
            s.units.save_request(a.id, asked)
        with pytest.raises(StaleWriteError):  # nor moved twice from the same version
            s.units.save(a.id, replace(moved, owner_id=a.teacher.id))
    assert len(mail.sent) == 2

    # The other college sees none of it.
    with session(b.id) as s:
        with pytest.raises(NotFoundError):
            s.units.get_request(b.id, asked.id)
        assert s.units.transfers(b.id, unit_id) == []
        assert s.units.requests(b.id) == []

    # Grants: transfers are history (no UPDATE, no DELETE), requests are never deleted.
    with test_database.app(a.id) as conn:
        for statement in (
            "UPDATE unit_transfers SET reason = 'x'",
            "DELETE FROM unit_transfers",
            "DELETE FROM delegation_requests",
        ):
            assert _refused(conn, statement), statement
        # The table's own checks: a declined request without a note.
        with pytest.raises(errors.CheckViolation), conn.transaction():
            conn.execute(
                "UPDATE delegation_requests SET state = 'declined', closed_by = %s, "
                "closed_at = now() WHERE id = %s",
                (a.admin.id, asked.id),
            )


def _refused(conn: psycopg.Connection[tuple[object, ...]], statement: str) -> bool:
    try:
        with conn.transaction():
            conn.execute(statement.encode())
    except errors.InsufficientPrivilege:
        return True
    return False


def test_a_removed_user_round_trips(session: Opener) -> None:
    a = new_college(session, "TRR")
    with session(a.id) as s:
        gone = replace(a.teacher, active=False, removed_at=s.clock.now())
        s.users.save(a.id, gone)
    with session(a.id) as s:
        assert s.users.get(a.id, a.teacher.id) == gone


def test_a_request_must_be_the_colleges_own_unit(session: Opener) -> None:
    a = new_college(session, "TRX")
    b = new_college(session, "TRY")
    with session(a.id) as s:
        unit_id = unit_for(s, a.id, ci_shaped_blueprint(s, a).id)
    with session(b.id) as s, pytest.raises(NotFoundError):
        s.units.save_request(
            b.id,
            DelegationRequest(
                id=DelegationRequestId(uuid4()),
                college_id=b.id,
                unit_id=unit_id,
                requested_by=b.teacher.id,
                reason="Not mine",
                requested_at=s.clock.now(),
            ),
        )
