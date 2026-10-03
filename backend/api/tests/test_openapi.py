"""R3: docs/api/openapi.json is the API's contract and must match the code."""

import json

from tarn_api.openapi import SPEC_PATH, openapi_text


def test_published_openapi_document_is_current() -> None:
    assert SPEC_PATH.exists(), "run `make openapi`"
    assert SPEC_PATH.read_text() == openapi_text(), "docs/api/openapi.json is stale: make openapi"


def test_document_lists_the_auth_endpoints() -> None:
    paths = json.loads(openapi_text())["paths"]
    for path in (
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/password/forgot",
        "/api/v1/auth/password/reset",
        "/api/v1/auth/password/recover",
        "/api/v1/auth/recovery-codes",
        "/api/v1/registrations",
        "/api/v1/registrations/availability",
        "/api/v1/accounts",
        "/api/v1/roster/import",
        "/api/v1/students",
    ):
        assert path in paths, path
