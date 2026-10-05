"""Small value types shared by the whole domain."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from tarn_core.errors import InvariantError
from tarn_core.ids import CollegeId

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None

ZERO = Decimal(0)
ONE = Decimal(1)


def check_marks(name: str, value: Decimal, *, allow_zero: bool = True) -> None:
    """Marks are finite, non-negative ``Decimal`` values (positive when ``allow_zero`` is False)."""
    if not isinstance(value, Decimal) or not value.is_finite():
        raise InvariantError(f"{name} must be a finite Decimal, got {value!r}")
    if value < 0 or (not allow_zero and value == 0):
        bound = "non-negative" if allow_zero else "positive"
        raise InvariantError(f"{name} must be {bound}, got {value}")


def check_unit_interval(name: str, value: float | Decimal) -> None:
    if not 0 <= value <= 1:
        raise InvariantError(f"{name} must be in [0, 1], got {value}")


def check_text(name: str, value: str) -> None:
    if not value.strip():
        raise InvariantError(f"{name} must not be blank")


@dataclass(frozen=True, slots=True, kw_only=True)
class Box:
    """Axis-aligned rectangle in image pixels: (x0, y0) top-left, (x1, y1) bottom-right."""

    x0: int
    y0: int
    x1: int
    y1: int

    def __post_init__(self) -> None:
        if min(self.x0, self.y0) < 0:
            raise InvariantError(f"box corner must not be negative: {self}")
        if self.x0 >= self.x1 or self.y0 >= self.y1:
            raise InvariantError(f"box must have positive width and height: {self}")

    @property
    def area(self) -> int:
        return (self.x1 - self.x0) * (self.y1 - self.y0)


class ContentKind(StrEnum):
    """Kinds of versioned global content a score can depend on."""

    SUBJECT = "subject"
    QUESTION = "question"
    REFERENCE_ANSWER = "reference_answer"
    RUBRIC_CRITERION = "rubric_criterion"
    GLOSSARY = "glossary"
    REFERENCE_DIAGRAM = "reference_diagram"
    KEY_FILE = "key_file"
    BLUEPRINT = "blueprint"
    OCR_CALIBRATION = "ocr_calibration"
    SCORING_CALIBRATION = "scoring_calibration"


@dataclass(frozen=True, slots=True, kw_only=True)
class ContentRef:
    """Points at one exact version of a global content item (CLAUDE.md rule 11)."""

    kind: ContentKind
    id: UUID
    version: int

    def __post_init__(self) -> None:
        if self.version < 1:
            raise InvariantError(f"content version starts at 1, got {self.version}")


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineRef:
    """Name and version of an engine, scorer or recognizer that produced a result."""

    name: str
    version: str

    def __post_init__(self) -> None:
        check_text("engine name", self.name)
        check_text("engine version", self.version)


@dataclass(frozen=True, slots=True)
class BlobKey:
    """Object-store key: ``college/{id}/...`` for college data, ``global/...`` for content."""

    value: str

    def __post_init__(self) -> None:
        parts = self.value.split("/")
        if any(not p or p in {".", ".."} for p in parts) or len(parts) < 2:
            raise InvariantError(f"malformed blob key: {self.value!r}")
        if parts[0] == "global":
            return
        if parts[0] == "college" and len(parts) >= 3:
            try:
                UUID(parts[1])
            except ValueError:
                raise InvariantError(f"blob key has a bad college id: {self.value!r}") from None
            return
        raise InvariantError(f"blob key must start with college/{{id}}/ or global/: {self.value!r}")

    @property
    def college_id(self) -> CollegeId | None:
        """The owning college for ``college/`` keys, None for ``global/`` keys."""
        parts = self.value.split("/")
        return CollegeId(UUID(parts[1])) if parts[0] == "college" else None


def college_blob_key(college_id: CollegeId, *parts: str) -> BlobKey:
    return BlobKey("/".join(("college", str(college_id), *parts)))


def global_blob_key(*parts: str) -> BlobKey:
    return BlobKey("/".join(("global", *parts)))
