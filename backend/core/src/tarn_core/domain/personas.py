"""What each persona may do (P24, D177). The one table the API checks: routes ask for a
capability, never for a list of roles. A capability that no route checks is not defined here;
later prompts add theirs (the Governor: ``freeze_units``)."""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from tarn_core.domain.tenancy import Role, User


class Capability(StrEnum):
    MANAGE_SMES = "manage_smes"
    """SME accounts and their subjects (invite, disable, enable, force reset, unlock, grant)."""
    MANAGE_SUBJECTS = "manage_subjects"
    """The college's subject list: create, rename, retire (D160)."""
    MANAGE_STUDENTS = "manage_students"
    """The roster: import and search."""
    ASSIGN_UNITS = "assign_units"
    """Create units of work and assign each to an entitled SME (P26, D162)."""
    UPLOAD_BOOKLETS = "upload_booklets"
    """Upload scanned booklets into a unit, use a flagged page anyway, replace a booklet for a
    retake (D162: the admin's job; P26)."""
    REDISTRIBUTE_UNITS = "redistribute_units"
    """Move an open unit to another entitled SME, and answer the SMEs' delegation requests
    (P27, D163)."""
    REQUEST_REASSIGNMENT = "request_reassignment"
    """Ask the admin to move one of the SME's own units to someone else (P27)."""
    VIEW_PROGRESS = "view_progress"
    """Booklets and their processing status, without marks, page images or text (D167)."""
    AUTHOR_CONTENT = "author_content"
    """Questions, keys, rubrics, glossaries, files, reference diagrams and papers of the SME's
    subjects (D160, D165)."""
    EVALUATE = "evaluate"
    """Pages, text, segments, drawings, review, approval, result sheets of the SME's subjects."""


CAPABILITIES: Mapping[Role, frozenset[Capability]] = MappingProxyType(
    {
        Role.ADMIN: frozenset(
            {
                Capability.MANAGE_SMES,
                Capability.MANAGE_SUBJECTS,
                Capability.MANAGE_STUDENTS,
                Capability.ASSIGN_UNITS,
                Capability.UPLOAD_BOOKLETS,
                Capability.REDISTRIBUTE_UNITS,
                Capability.VIEW_PROGRESS,
            }
        ),
        Role.SME: frozenset(
            {
                Capability.VIEW_PROGRESS,
                Capability.AUTHOR_CONTENT,
                Capability.EVALUATE,
                Capability.REQUEST_REASSIGNMENT,
            }
        ),
        # The Governor only signs in until its portal is built (D159).
        Role.GOVERNOR: frozenset(),
    }
)


def can(user: User, capability: Capability) -> bool:
    """An active user whose persona has ``capability``."""
    return user.active and capability in CAPABILITIES[user.role]
