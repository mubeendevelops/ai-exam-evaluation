"""api/schemas/pagination.py — the two generic response shapes every
paginated or capped read in the API uses.

`Page[T]` is a LIST ENDPOINT's response: the caller asked for a page via
api/deps/pagination.py, and got one back — `{items, total, limit, offset}`,
the shape named for every new list endpoint in this pass. `total` ignores
limit/offset (it is the count of everything the filters match), which is what
lets a client page without guessing when it has reached the end.

`CappedList[T]` is for a NESTED collection inside a single-resource response
(GET /results/{id}'s ledger history, review log, per-region components) — see
core/pagination.py::cap()'s docstring for why that is a different shape from
a page: there was no limit/offset request, just a safety cap on a list with
no inherent bound.

GET /api/v1/questions keeps its own `QuestionListResponse` (api/schemas/
questions.py) rather than moving to `Page[QuestionSummary]` here — it shipped
first, with a `questions` field name real clients may already depend on, and
renaming that key is a breaking change this pass was not asked to make. Every
NEW list endpoint uses `Page[T]` from the start.
"""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

ItemT = TypeVar("ItemT")


class Page(BaseModel, Generic[ItemT]):
    """A page of a list endpoint: `{items, total, limit, offset}`."""

    model_config = ConfigDict(extra="forbid")

    items: list[ItemT]
    total: int = Field(
        ge=0,
        description="Rows matching the filters, ignoring limit/offset — lets "
                    "a client page without guessing when it has reached the "
                    "end.",
    )
    limit: int
    offset: int


class CappedList(BaseModel, Generic[ItemT]):
    """A nested, unpaginated-but-capped collection inside a single-resource
    response. See core/pagination.py::cap()."""

    model_config = ConfigDict(extra="forbid")

    items: list[ItemT]
    total: int = Field(
        ge=0, description="The TRUE count before capping, not len(items)."
    )
    truncated: bool = Field(
        description="True when `total` exceeds `items` — more rows exist "
                    "than were returned."
    )
