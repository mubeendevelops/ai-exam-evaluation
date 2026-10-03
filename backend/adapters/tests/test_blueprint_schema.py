"""Contract: ``docs/api/blueprint.schema.json`` (the published schema, R3) and the validator in
the core agree on structure. Rules that are arithmetic (totals, sub-part sums, equal OR marks)
are outside JSON Schema and only the core checks them."""

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from tarn_core.services.blueprint_document import check_document

DOCS = Path(__file__).resolve().parents[3] / "docs" / "api"
SCHEMA = json.loads((DOCS / "blueprint.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=Draft202012Validator.FORMAT_CHECKER)


def example(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((DOCS / f"blueprint.example-{name}.json").read_text())
    return loaded


def schema_accepts(document: object) -> bool:
    return VALIDATOR.is_valid(document)


def core_accepts(document: object) -> bool:
    return check_document(document).valid


def test_the_schema_is_a_valid_versioned_schema() -> None:
    Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["$id"].endswith(":1.0")
    assert SCHEMA["properties"]["schema_version"] == {"const": "1.0"}


@pytest.mark.parametrize("name", ["ci", "ipr", "objective"])
def test_examples_pass_both(name: str) -> None:
    assert schema_accepts(example(name))
    assert core_accepts(example(name))


def test_minimal_document_passes_both() -> None:
    minimal = {
        "schema_version": "1.0",
        "title": "T",
        "course_code": "C",
        "subject_id": "00000000-0000-4000-8000-000000000001",
        "total_marks": 2,
        "sections": [
            {
                "label": "A",
                "method": "diagram",
                "items": [{"type": "question", "label": "1", "marks": 2}],
            }
        ],
    }
    assert schema_accepts(minimal) and core_accepts(minimal)


def _set(path: list[str | int], value: object) -> Callable[[dict[str, Any]], None]:
    def apply(d: dict[str, Any]) -> None:
        node: Any = d
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    return apply


def _drop(path: list[str | int]) -> Callable[[dict[str, Any]], None]:
    def apply(d: dict[str, Any]) -> None:
        node: Any = d
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]

    return apply


Q0: list[str | int] = ["sections", 0, "items", 0]
OR_ITEM: list[str | int] = ["sections", 2, "items", 0]  # in the IPR example
BREAKERS: dict[str, tuple[str, Callable[[dict[str, Any]], None]]] = {
    "extra top-level field": ("ci", _set(["extra"], 1)),
    "missing title": ("ci", _drop(["title"])),
    "empty title": ("ci", _set(["title"], "")),
    "wrong schema version": ("ci", _set(["schema_version"], "2.0")),
    "subject not a uuid": ("ci", _set(["subject_id"], "x")),
    "zero total": ("ci", _set(["total_marks"], 0)),
    "total as text": ("ci", _set(["total_marks"], "50")),
    "zero duration": ("ci", _set(["duration_minutes"], 0)),
    "negative marking 1": ("ci", _set(["negative_marking"], 1)),
    "mark step 0.3": ("ci", _set(["mark_step"], 0.3)),
    "no sections": ("ci", _set(["sections"], [])),
    "unknown method": ("ci", _set(["sections", 0, "method"], "essay")),
    "section without method": ("ci", _drop(["sections", 0, "method"])),
    "section extra field": ("ci", _set(["sections", 0, "extra"], 1)),
    "any without n": ("ci", _set(["sections", 0, "choice"], {"rule": "any"})),
    "all with n": ("ci", _set(["sections", 0, "choice"], {"rule": "all", "n": 2})),
    "n is zero": ("ci", _set(["sections", 0, "choice", "n"], 0)),
    "null choice": ("ci", _set(["sections", 0, "choice"], None)),
    "no items": ("ci", _set(["sections", 0, "items"], [])),
    "unknown item type": ("ci", _set([*Q0, "type"], "group")),
    "zero marks": ("ci", _set([*Q0, "marks"], 0)),
    "question without label": ("ci", _drop([*Q0, "label"])),
    "question id not a uuid": ("ci", _set([*Q0, "question_id"], "abc")),
    "or with one alternative": (
        "ipr",
        _set([*OR_ITEM, "alternatives"], [{"label": "12", "marks": 15}]),
    ),
    "part without marks": ("ipr", _drop([*OR_ITEM, "alternatives", 0, "parts", 0, "marks"])),
    "step with zero marks": (
        "objective",
        _set(["sections", 1, "items", 0, "steps", 0, "marks"], 0),
    ),
    "typed alternative": ("ipr", _set([*OR_ITEM, "alternatives", 0, "type"], "question")),
}


@pytest.mark.parametrize("name", sorted(BREAKERS))
def test_structural_mistakes_are_refused_by_both(name: str) -> None:
    base, apply = BREAKERS[name]
    document = copy.deepcopy(example(base))
    apply(document)
    assert not schema_accepts(document), "the published schema accepted it"
    assert not core_accepts(document), "the core validator accepted it"


def test_arithmetic_rules_are_only_enforced_by_the_core() -> None:
    document = example("ci")
    document["total_marks"] = 51
    assert schema_accepts(document)
    assert not core_accepts(document)
