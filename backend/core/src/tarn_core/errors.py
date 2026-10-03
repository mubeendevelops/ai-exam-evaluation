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
