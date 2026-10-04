"""Errors raised by the core. Adapters translate them into HTTP codes, exit codes, etc."""


class DomainError(Exception):
    """Base class for every error the core raises on purpose."""


class InvariantError(DomainError, ValueError):
    """A domain value was built in a state its rules forbid."""


class NotFoundError(DomainError, LookupError):
    """The item does not exist, or exists in another college (the two are indistinguishable)."""


class NotOwnerError(DomainError, PermissionError):
    """Global content may be edited only by teachers of its owning college (design decision 3)."""


class TenantViolationError(DomainError, PermissionError):
    """A write tried to put one college's row through another college's call."""


class PermissionDeniedError(DomainError, PermissionError):
    """The caller's role does not allow the action (e.g. a teacher disabling an account)."""


class AlreadyExistsError(DomainError, ValueError):
    """A unique name is taken: an institution id, or an email within a college."""


class PasswordPolicyError(DomainError, ValueError):
    """A new password breaks the tenant's policy. ``reasons`` are safe to show the user."""

    def __init__(self, reasons: tuple[str, ...]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class TokenError(DomainError, ValueError):
    """A link or session token is unknown, used, expired or revoked (one message for all)."""


class UnreadableFileError(DomainError, ValueError):
    """An uploaded file is not a readable PDF or image (damaged, encrypted, or another type)."""


class UnsupportedFileError(DomainError, ValueError):
    """The uploaded file is not a PDF, JPEG or PNG (judged by its first bytes, not its name)."""


class UploadTooLargeError(DomainError, ValueError):
    """The upload has more bytes or more files than the configured limit."""


class QueueFullError(DomainError, ValueError):
    """The teacher already has the maximum number of booklets waiting to be processed."""


class DuplicateBookletError(DomainError, ValueError):
    """A booklet with the same file hash exists in this college; upload again with
    ``allow_duplicate`` to create a second copy on purpose."""

    def __init__(self, duplicates: tuple[object, ...]) -> None:
        super().__init__("This file was uploaded before in your college.")
        self.duplicates = duplicates


class EngineFailedError(DomainError, RuntimeError):
    """An OCR engine could not read a page (it raised, or its service refused). The reader
    records the engine and carries on with the others (design.md "Reliability")."""


class EngineTimeoutError(EngineFailedError, TimeoutError):
    """An OCR engine took longer than its time limit."""
