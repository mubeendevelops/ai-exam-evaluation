"""Smoke tests for tarn_core, including a runtime check that it stays pure."""

import subprocess
import sys

import tarn_core

FORBIDDEN_TOP_LEVEL = (
    "fastapi",
    "sqlalchemy",
    "psycopg",
    "boto3",
    "requests",
    "httpx",
    "azure",
    "torch",
    "tarn_adapters",
)


def test_version() -> None:
    assert tarn_core.__version__ == "0.0.0"


def test_importing_core_loads_no_forbidden_module() -> None:
    # A fresh interpreter, so modules imported by other tests cannot hide a violation.
    code = (
        "import sys, tarn_core\n"
        f"bad = [m for m in {FORBIDDEN_TOP_LEVEL!r} if m in sys.modules]\n"
        "print(','.join(bad))\n"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == ""
