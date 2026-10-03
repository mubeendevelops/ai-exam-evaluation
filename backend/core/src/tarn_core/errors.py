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
