"""O7 is accepted, not fixed (P21, D147): the application role may set ``app.college_id``
itself, so row-level security holds only while no request can inject SQL. This test keeps the
condition true: no runtime code builds SQL text from strings. Every value goes in as a bound
parameter (SQLAlchemy Core, ``text(...)`` with ``:name``), and identifiers go through psycopg's
``sql.Identifier``. Migrations and test helpers (owner role, fixed names) are not scanned."""

import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
SQL_CALLS = {"text", "execute", "exec_driver_sql", "executemany"}


def runtime_sources() -> list[Path]:
    files = []
    for package in ("core", "adapters", "api", "worker", "cli"):
        for path in (BACKEND / package / "src").rglob("*.py"):
            parts = set(path.parts)
            if "migrations" in parts or "testing" in parts or path.name == "testing.py":
                continue
            files.append(path)
    return files


def _called(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _built_string(node: ast.expr) -> bool:
    """An f-string, ``"...".format(...)``, ``"..." % x`` or a ``+`` with a string literal."""
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr == "format" and isinstance(node.func.value, ast.Constant)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
        sides = (node.left, node.right)
        return any(isinstance(s, ast.Constant) and isinstance(s.value, str) for s in sides) or any(
            _built_string(s) for s in sides
        )
    return False


def formatted_sql(source: str) -> list[int]:
    """Lines where a SQL call gets a string built at run time as its statement."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and _called(node) in SQL_CALLS
            and node.args
            and _built_string(node.args[0])
        ):
            found.append(node.lineno)
    return found


def test_the_scan_sees_the_runtime_code() -> None:
    names = {p.name for p in runtime_sources()}
    assert {"repositories.py", "jobs.py", "database.py", "store.py"} <= names
    assert len(names) > 100


@pytest.mark.parametrize(
    "snippet",
    [
        'conn.execute(text(f"SELECT * FROM booklets WHERE id = {booklet_id}"))',
        "conn.execute(text(\"SELECT * FROM t WHERE usn = '%s'\" % usn))",
        'conn.exec_driver_sql("DELETE FROM t WHERE id = {}".format(x))',
        'conn.execute(text("SELECT * FROM t WHERE name = \'" + name + "\'"))',
    ],
)
def test_the_scan_catches_formatted_sql(snippet: str) -> None:
    assert formatted_sql(snippet), snippet


def test_bound_parameters_pass() -> None:
    ok = 'conn.execute(text("SELECT set_config(\'app.college_id\', :c, true)"), {"c": cid})'
    assert formatted_sql(ok) == []


def test_no_runtime_code_formats_sql() -> None:
    offenders = [
        f"{path.relative_to(BACKEND)}:{line}"
        for path in runtime_sources()
        for line in formatted_sql(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
