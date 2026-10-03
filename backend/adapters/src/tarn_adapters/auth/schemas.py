"""JSON Schema validation of identity and tenant records (``docs/auth/*.schema.json``; the
package carries identical copies, checked by a test)."""

import json
from functools import lru_cache
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from tarn_core.domain.common import JsonValue
from tarn_core.errors import InvariantError

IDENTITY_RECORD = "identity-record.schema.json"
TENANT_REGISTRY = "tenant-registry.schema.json"


def schema_text(name: str) -> str:
    return resources.files("tarn_adapters.auth").joinpath(f"schemas/{name}").read_text()


@lru_cache(maxsize=2)
def _validator(name: str) -> Draft202012Validator:
    schema: dict[str, Any] = json.loads(schema_text(name))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _validate(name: str, record: JsonValue) -> None:
    errors = sorted(_validator(name).iter_errors(record), key=lambda e: list(e.path))
    if errors:
        # The path and the failing keyword only: never the value (it may be a hash or email).
        where = ", ".join(
            f"{'/'.join(str(p) for p in e.path) or '(root)'}: {e.validator}" for e in errors[:5]
        )
        raise InvariantError(f"{name.removesuffix('.schema.json')} record is not valid ({where})")


class JsonSchemaValidator:
    def validate_tenant(self, record: JsonValue) -> None:
        _validate(TENANT_REGISTRY, record)

    def validate_identity(self, record: JsonValue) -> None:
        _validate(IDENTITY_RECORD, record)
