"""Domain errors -> HTTP. Messages are the core's own (they never echo database text)."""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from tarn_core.errors import (
    AlreadyExistsError,
    DomainError,
    DuplicateBookletError,
    InvariantError,
    NotFoundError,
    NotOwnerError,
    PasswordPolicyError,
    PermissionDeniedError,
    QueueFullError,
    TenantViolationError,
    TokenError,
    UnsupportedFileError,
    UploadTooLargeError,
)

_STATUS: list[tuple[type[DomainError], int]] = [
    (PasswordPolicyError, status.HTTP_422_UNPROCESSABLE_CONTENT),
    (TokenError, status.HTTP_400_BAD_REQUEST),
    (AlreadyExistsError, status.HTTP_409_CONFLICT),
    (DuplicateBookletError, status.HTTP_409_CONFLICT),
    (QueueFullError, status.HTTP_429_TOO_MANY_REQUESTS),
    (UploadTooLargeError, status.HTTP_413_CONTENT_TOO_LARGE),
    (UnsupportedFileError, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE),
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
    if isinstance(exc, (NotFoundError, TenantViolationError)):
        return JSONResponse({"detail": "Not found."}, code)
    return JSONResponse({"detail": str(exc)}, code)


def install(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error)
