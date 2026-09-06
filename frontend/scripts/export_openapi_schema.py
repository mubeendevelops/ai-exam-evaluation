"""frontend/scripts/export_openapi_schema.py — dumps api/'s OpenAPI schema to stdout.

Imports `api.main.create_app` and calls `.openapi()` directly rather than
hitting a running server's `/openapi.json`. The schema is generated from the
Pydantic models and route declarations alone, so this needs no Postgres, no
.env, and no `uvicorn` process — only the same Python environment the API
itself runs in (repo root's `.venv-paddleocr`, or any venv with `requirements.txt`
installed).

Run from anywhere; it locates the repo root relative to this file. Used by
`npm run generate:api` (see package.json) — do not call openapi-typescript
against a hand-maintained copy of this output, always regenerate.
"""
from __future__ import annotations

import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.main import create_app  # noqa: E402

if __name__ == "__main__":
    schema = create_app().openapi()
    json.dump(schema, sys.stdout)
