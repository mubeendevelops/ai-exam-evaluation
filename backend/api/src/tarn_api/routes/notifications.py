"""The SME's notifications (P37, D227, D241): ``/api/v1/notifications``. Their own notices only;
another user's notice is 404 like a missing one. The bell polls ``/unread-count``."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from tarn_api.backends import unit_of_work
from tarn_api.schemas import (
    ErrorOut,
    MarkedReadOut,
    NotificationOut,
    NotificationPageOut,
    UnreadCountOut,
)
from tarn_api.security import BackendsDep, PrincipalDep, current_user
from tarn_core.domain.notifications import PAGE_MAX, Notification
from tarn_core.domain.personas import Capability
from tarn_core.ids import NotificationId

router = APIRouter(
    prefix="/api/v1/notifications",
    tags=["notifications"],
    responses={401: {"model": ErrorOut}, 403: {"model": ErrorOut}},
)


def notification_out(n: Notification) -> NotificationOut:
    p = n.payload
    request = p.get("request_id")
    return NotificationOut(
        id=n.id,
        kind=n.kind.value,
        unit_id=n.unit_id,
        unit_code=p.get("unit_code", ""),
        due_date=p.get("due_date"),
        request_id=None if request is None else UUID(request),
        note=p.get("note"),
        created_at=n.created_at,
        read_at=n.read_at,
    )


@router.get(
    "",
    response_model=NotificationPageOut,
    summary="My notifications (SME)",
    description="Newest first, paged, with the total and unread counts.",
)
def list_notifications(
    who: PrincipalDep,
    backends: BackendsDep,
    limit: Annotated[int, Query(ge=1, le=PAGE_MAX)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> NotificationPageOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.VIEW_OWN_WORK)
        page = unit.notifications.page(who.college_id, who.user_id, limit=limit, offset=offset)
    return NotificationPageOut(
        items=[notification_out(n) for n in page.items],
        total=page.total,
        unread=page.unread,
        limit=limit,
        offset=offset,
    )


@router.get("/unread-count", response_model=UnreadCountOut, summary="My unread notifications")
def unread_count(who: PrincipalDep, backends: BackendsDep) -> UnreadCountOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.VIEW_OWN_WORK)
        return UnreadCountOut(unread=unit.notifications.unread(who.college_id, who.user_id))


@router.post(
    "/{notification_id}/read",
    response_model=NotificationOut,
    summary="Mark one notification read",
    responses={404: {"model": ErrorOut}},
)
def mark_read(notification_id: UUID, who: PrincipalDep, backends: BackendsDep) -> NotificationOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.VIEW_OWN_WORK)
        found = unit.notifications.mark_read(
            who.college_id, who.user_id, NotificationId(notification_id)
        )
    return notification_out(found)


@router.post("/read-all", response_model=MarkedReadOut, summary="Mark all my notifications read")
def mark_all_read(who: PrincipalDep, backends: BackendsDep) -> MarkedReadOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.VIEW_OWN_WORK)
        marked = unit.notifications.mark_all_read(who.college_id, who.user_id)
    return MarkedReadOut(marked=marked)
