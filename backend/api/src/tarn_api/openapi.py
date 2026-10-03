"""The OpenAPI document (R3): written to ``docs/api/openapi.json`` by ``tarn openapi`` on every
build (``make openapi``, run by ``make ci`` and CI), and checked for staleness by a test."""

import json
from pathlib import Path

from tarn_adapters.config import Settings
from tarn_api.app import create_app

SPEC_PATH = Path(__file__).resolve().parents[4] / "docs" / "api" / "openapi.json"


def openapi_text() -> str:
    # Defaults only: the document must not depend on the local .env.
    app = create_app(Settings(_env_file=None))
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"


def write(path: Path = SPEC_PATH) -> bool:
    """Write the document; True if the file changed."""
    text = openapi_text()
    if path.exists() and path.read_text() == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return True


def main(argv: list[str] | None = None) -> int:
    """``python -m tarn_api.openapi [--check]``: write the document, or fail if it is stale."""
    import sys

    args = sys.argv[1:] if argv is None else argv
    if "--check" in args:
        if not SPEC_PATH.exists() or SPEC_PATH.read_text() != openapi_text():
            print(f"{SPEC_PATH} is out of date: run `make openapi`", file=sys.stderr)  # noqa: T201
            return 1
        return 0
    changed = write()
    print(f"{SPEC_PATH} {'written' if changed else 'unchanged'}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
