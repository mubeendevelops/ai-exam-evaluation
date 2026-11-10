"""The SME's global search (P37, D228, D243): one query over what the SME can reach, grouped,
at most ``GROUP_MAX`` hits per group. Units and their students: only the units the SME owns now;
papers, schemas and questions: only the SME's subjects. The query text is never logged."""

import unicodedata
from dataclasses import dataclass
from datetime import date

from tarn_core.errors import InvariantError
from tarn_core.ids import BlueprintId, BookletId, QuestionId, SubjectId, UnitId

QUERY_MIN = 2
QUERY_MAX = 80
GROUP_MAX = 5


def search_text(raw: str) -> str:
    """The query as searched: NFKC, trimmed, inner runs of spaces made one. 2 to 80 characters
    (``InvariantError`` otherwise; the message never repeats the text)."""
    text = " ".join(unicodedata.normalize("NFKC", raw).split())
    if not QUERY_MIN <= len(text) <= QUERY_MAX:
        raise InvariantError(f"Search for {QUERY_MIN} to {QUERY_MAX} characters.")
    return text


@dataclass(frozen=True, slots=True, kw_only=True)
class StudentHit:
    """A booklet of one of the SME's units whose student matches."""

    booklet_id: BookletId
    unit_id: UnitId
    name: str
    usn: str


@dataclass(frozen=True, slots=True, kw_only=True)
class UnitHit:
    unit_id: UnitId
    code: str
    paper_title: str
    exam_code: str
    year: str
    due_date: date
    state: str


@dataclass(frozen=True, slots=True, kw_only=True)
class BlueprintHit:
    blueprint_id: BlueprintId
    title: str
    exam_code: str
    descriptor: str
    subject_id: SubjectId


@dataclass(frozen=True, slots=True, kw_only=True)
class QuestionHit:
    question_id: QuestionId
    code: str
    text: str
    """The first line of the question, at most 120 characters."""
    subject_id: SubjectId


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchResults:
    units: tuple[UnitHit, ...] = ()
    students: tuple[StudentHit, ...] = ()
    papers: tuple[BlueprintHit, ...] = ()
    schemas: tuple[BlueprintHit, ...] = ()
    questions: tuple[QuestionHit, ...] = ()


__all__ = [
    "GROUP_MAX",
    "QUERY_MAX",
    "QUERY_MIN",
    "BlueprintHit",
    "QuestionHit",
    "SearchResults",
    "StudentHit",
    "UnitHit",
    "search_text",
]
