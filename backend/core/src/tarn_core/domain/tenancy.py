"""Tenants and the people in them. Every type here is college data."""

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

    def __post_init__(self) -> None:
        check_text("student name", self.name)
        check_text("USN", self.usn)
        if self.usn != normalise_usn(self.usn):
            raise InvariantError(f"USN must be normalised (upper case, no spaces): {self.usn!r}")


def normalise_usn(raw: str) -> str:
    return "".join(raw.split()).upper()
