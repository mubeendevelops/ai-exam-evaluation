"""Tenants and the people in them. Every type here is college data."""

import re
from dataclasses import dataclass
from enum import StrEnum

from tarn_core.domain.common import check_text
from tarn_core.errors import InvariantError
from tarn_core.ids import CollegeId, StudentId, UserId


@dataclass(frozen=True, slots=True, kw_only=True)
class College:
    id: CollegeId
    name: str
    code: str

    def __post_init__(self) -> None:
        check_text("college name", self.name)
        check_text("college code", self.code)


class Role(StrEnum):
    ADMIN = "admin"
    TEACHER = "teacher"


@dataclass(frozen=True, slots=True, kw_only=True)
class User:
    """Profile and role only; credentials live in the separate identity store (P4)."""

    id: UserId
    college_id: CollegeId
    display_name: str
    email: str
    role: Role
    active: bool = True

    def __post_init__(self) -> None:
        check_text("display name", self.display_name)
        if "@" not in self.email:
            raise InvariantError("email must contain '@'")


@dataclass(frozen=True, slots=True, kw_only=True)
class Student:
    """A roster entry. Booklets are linked to a student by roster pick (D17)."""

    id: StudentId
    college_id: CollegeId
    name: str
    usn: str
    class_section: str = ""

    def __post_init__(self) -> None:
        check_text("student name", self.name)
        check_text("USN", self.usn)
        if self.usn != normalise_usn(self.usn):
            raise InvariantError(f"USN must be normalised (upper case, no spaces): {self.usn!r}")
        if self.class_section != self.class_section.strip():
            raise InvariantError("class/section must be trimmed")


def normalise_usn(raw: str) -> str:
    return "".join(raw.split()).upper()


# Institution ID, as in MainLogin.html "Tenant Registration": upper-case letters, digits and
# _ - * &, at most 20 characters, no spaces.
INSTITUTION_ID_MAX = 20
_INSTITUTION_ID = re.compile(r"[A-Z0-9_\-*&]{1,20}")


def institution_id_problem(value: str) -> str | None:
    """Why ``value`` is not a valid Institution ID, or None when it is."""
    if not value:
        return "Institution ID is required."
    if len(value) > INSTITUTION_ID_MAX:
        return f"Institution ID has at most {INSTITUTION_ID_MAX} characters."
    if any(ch.isspace() for ch in value):
        return "Institution ID cannot contain spaces."
    if not _INSTITUTION_ID.fullmatch(value):
        return "Use upper-case letters, digits and _ - * & only."
    return None


def normalise_institution_id(raw: str) -> str:
    """For sign-in: trimmed and upper-cased. Registration takes the ID exactly as typed."""
    return raw.strip().upper()


def canonical_email(raw: str) -> str:
    """Emails are compared trimmed and lower-cased (the whole address)."""
    return raw.strip().lower()


def check_email(value: str) -> None:
    local, at, domain = value.rpartition("@")
    if not at or not local or "." not in domain or any(ch.isspace() for ch in value):
        raise InvariantError("email address is not valid")
