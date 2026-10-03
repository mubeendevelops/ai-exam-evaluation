"""PostgreSQL adapter pieces that need no database: JSON codec, URLs, error translation."""

from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy.exc import DBAPIError

from tarn_adapters.postgres import codec
from tarn_adapters.postgres.database import libpq_url, sqlalchemy_url
from tarn_adapters.postgres.repositories import translate
from tarn_core.domain.common import ContentKind, ContentRef
from tarn_core.domain.content import (
    CriterionType,
    DiagramComponent,
    DiagramParams,
    ListItem,
    ListParams,
    LlmParams,
    NumericParams,
    SemanticParams,
)
from tarn_core.errors import (
    DomainError,
    InvariantError,
    NotFoundError,
    NotOwnerError,
    TenantViolationError,
)
from tarn_core.ids import ReferenceDiagramId
from tarn_core.testing import InMemory
from tarn_core.testing.builders import add_college, ipr_shaped_blueprint


def test_blueprint_sections_roundtrip() -> None:
    mem = InMemory()
    blueprint = ipr_shaped_blueprint(mem, add_college(mem, "C"))
    assert codec.sections_from_json(codec.sections_to_json(blueprint.sections)) == (
        blueprint.sections
    )


@pytest.mark.parametrize(
    ("kind", "params"),
    [
        (
            CriterionType.LIST,
            ListParams(items=(ListItem(term="a", synonyms=("b",)),), required_count=1),
        ),
        (CriterionType.NUMERIC, NumericParams(expected=Decimal("1.10"), tolerance=Decimal("0"))),
        (CriterionType.SEMANTIC, SemanticParams(reference_statement="x")),
        (
            CriterionType.DIAGRAM,
            DiagramParams(
                reference_diagram_id=ReferenceDiagramId(UUID(int=7)),
                component=DiagramComponent.EDGES,
            ),
        ),
        (CriterionType.LLM, LlmParams(instructions="y")),
    ],
)
def test_criterion_params_roundtrip(
    kind: CriterionType,
    params: ListParams | NumericParams | SemanticParams | DiagramParams | LlmParams,
) -> None:
    assert codec.params_from_json(kind, codec.params_to_json(params)) == params


def test_content_refs_roundtrip_in_a_stable_order() -> None:
    refs = frozenset(
        ContentRef(kind=k, id=UUID(int=n), version=n)
        for n, k in enumerate((ContentKind.RUBRIC_CRITERION, ContentKind.QUESTION), start=1)
    )
    encoded = codec.refs_to_json(refs)
    assert codec.refs_from_json(encoded) == refs
    assert encoded == codec.refs_to_json(frozenset(reversed(list(refs))))


def test_bad_json_shape_is_reported() -> None:
    with pytest.raises(codec.CodecError):
        codec.sections_from_json({"not": "a list"})


def test_urls() -> None:
    url = "postgresql://tarn_app:p%40ss@localhost:5432/tarn"
    assert sqlalchemy_url(url) == "postgresql+psycopg://tarn_app:p%40ss@localhost:5432/tarn"
    assert libpq_url(sqlalchemy_url(url)) == url
    with pytest.raises(ValueError):
        sqlalchemy_url("mysql://x@y/z")


class _DriverError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__("row (Secret Student, TST26A0001) failed")
        self.sqlstate = sqlstate


@pytest.mark.parametrize(
    ("sqlstate", "refused", "expected"),
    [
        ("42501", TenantViolationError, TenantViolationError),
        ("42501", NotOwnerError, NotOwnerError),
        ("23503", TenantViolationError, NotFoundError),
        ("23505", TenantViolationError, InvariantError),
        ("23514", TenantViolationError, InvariantError),
    ],
)
def test_database_errors_become_domain_errors(
    sqlstate: str, refused: type[DomainError], expected: type[DomainError]
) -> None:
    exc = DBAPIError("INSERT ...", None, _DriverError(sqlstate))
    with pytest.raises(expected) as raised:
        translate(exc, "booklet 1", refused)
    # The database's message (which can contain row values) is not repeated.
    assert "Secret Student" not in str(raised.value)


def test_unknown_database_errors_pass_through() -> None:
    exc = DBAPIError("SELECT ...", None, _DriverError("57014"))
    with pytest.raises(DBAPIError):
        translate(exc, "query", TenantViolationError)
