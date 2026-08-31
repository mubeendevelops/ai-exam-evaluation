"""tests/conftest.py — shared fixtures for the characterization-test suite.

No python-dotenv dependency: `.env` parsing is done here with a tiny
KEY=VALUE parser, mirroring what the shell scripts already do (`set -a &&
source .env && set +a`) but from Python, and only filling in values that
aren't already set in the real environment (os.environ.setdefault) so a
CI/host env always wins over the repo's .env.
"""
from __future__ import annotations

import os
import pathlib

import pytest

import core.db


def _load_dotenv(path: pathlib.Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)


@pytest.fixture(scope="session", autouse=True)
def _dotenv():
    """Parses repo-root .env into os.environ once per test session, before
    any test runs, so core.db.get_connection() (which reads os.environ
    directly) works without every test having to source .env by hand."""
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    _load_dotenv(repo_root / ".env")


@pytest.fixture
def db_conn():
    """A live Postgres connection with the platform-admin RLS bypass set
    for the (implicitly-opened) transaction. Never committed — rolled back
    and closed on teardown so tests can't leave data behind."""
    conn = core.db.get_connection()
    cur = conn.cursor()
    cur.execute("SET LOCAL app.is_platform_admin = 'true'")
    cur.close()
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def tenant_conn():
    """Factory fixture: tenant_conn(college_id) -> connection, scoped to
    that college via RLS (app.current_college_id). Every connection handed
    out is tracked and rolled back + closed at teardown."""
    handed_out: list = []

    def make(college_id):
        conn = core.db.get_connection()
        cur = conn.cursor()
        cur.execute("SET LOCAL app.current_college_id = %s", (str(college_id),))
        cur.close()
        handed_out.append(conn)
        return conn

    yield make

    for conn in handed_out:
        conn.rollback()
        conn.close()


class _StubLLM:
    """Mutable controller handed to tests using `stub_llm` — set `.text`
    (and optionally `.usage`) before calling code that hits
    core.llm._generate."""

    def __init__(self):
        self.text = "stub response"
        self.usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

    def _generate(self, prompt, return_metrics=False):
        if return_metrics:
            return self.text, self.usage
        return self.text


@pytest.fixture
def stub_llm(monkeypatch):
    """Monkeypatches core.llm._generate with a fake honoring the real dual
    signature (plain str, or (str, usage) when return_metrics=True). Tests
    set `stub_llm.text` / `stub_llm.usage` before triggering the call."""
    controller = _StubLLM()
    monkeypatch.setattr("core.llm._generate", controller._generate)
    return controller
