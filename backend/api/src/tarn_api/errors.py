"""Domain errors -> HTTP. Messages are the core's own (they never echo database text)."""

from datetime import datetime

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from tarn_core.errors import (
    AlreadyExistsError,
    BookletLockedError,
    DomainError,
    DuplicateBookletError,
    IllegalTransitionError,
    InvariantError,
    NotFoundError,
    NotOwnerError,
    PasswordPolicyError,
    PermissionDeniedError,
    QueueFullError,
    RescorePendingError,
    StaleWriteError,
    TenantViolationError,
    TokenError,
    UnsupportedFileError,
    UploadTooLargeError,
)
from tarn_core.services.scoring.booklet import AnswerApprovedError

_STATUS: list[tuple[type[DomainError], int]] = [
    (PasswordPolicyError, status.HTTP_422_UNPROCESSABLE_CONTENT),
    (TokenError, status.HTTP_400_BAD_REQUEST),
    (AlreadyExistsError, status.HTTP_409_CONFLICT),
    (DuplicateBookletError, status.HTTP_409_CONFLICT),
    (QueueFullError, status.HTTP_429_TOO_MANY_REQUESTS),
    (UploadTooLargeError, status.HTTP_413_CONTENT_TOO_LARGE),
    (UnsupportedFileError, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE),
    # The review (P15): a stale screen, a move the state machine refuses, a suggestion still
    # on its way, an approved answer edited without reopening it: 409; another teacher has
    # the booklet open, or the caller has not opened it: 423.
    (StaleWriteError, status.HTTP_409_CONFLICT),
    (IllegalTransitionError, status.HTTP_409_CONFLICT),
    (RescorePendingError, status.HTTP_409_CONFLICT),
    (AnswerApprovedError, status.HTTP_409_CONFLICT),
    (BookletLockedError, status.HTTP_423_LOCKED),
    (PermissionDeniedError, status.HTTP_403_FORBIDDEN),
    (NotOwnerError, status.HTTP_403_FORBIDDEN),
    # Another college's rows look exactly like missing rows.
    (TenantViolationError, status.HTTP_404_NOT_FOUND),
    (NotFoundError, status.HTTP_404_NOT_FOUND),
    (InvariantError, status.HTTP_422_UNPROCESSABLE_CONTENT),
]


def _domain_error(_request: Request, exc: Exception) -> JSONResponse:
    code = next((c for kind, c in _STATUS if isinstance(exc, kind)), status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, PasswordPolicyError):
        return JSONResponse(
            {"detail": "The password does not meet the policy.", "reasons": list(exc.reasons)},
            code,
        )
    if isinstance(exc, DuplicateBookletError):
        return JSONResponse(
            {"detail": str(exc), "duplicate_of": [str(d) for d in exc.duplicates]}, code
        )
    if isinstance(exc, BookletLockedError):
        expires = exc.expires_at
        return JSONResponse(
            {
                "detail": str(exc),
                "holder_id": None if exc.holder is None else str(exc.holder),
                "expires_at": expires.isoformat() if isinstance(expires, datetime) else None,
            },
            code,
        )
    if isinstance(exc, (NotFoundError, TenantViolationError)):
        return JSONResponse({"detail": "Not found."}, code)
    return JSONResponse({"detail": str(exc)}, code)


def install(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error)
