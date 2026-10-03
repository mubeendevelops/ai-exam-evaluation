"""Tarn AI Evaluation core: plain data in, plain data out.

This package performs no I/O and imports no web, database or cloud library.
Everything external goes through a port (a ``typing.Protocol``) implemented by an
adapter in ``tarn_adapters``. Enforced by the import-linter contract in
``backend/pyproject.toml``.

- ``domain``: frozen dataclasses with invariants (content, blueprint, booklet, scoring, ...)
- ``ports``: the Protocols adapters implement
- ``services``: use cases; every one that touches college data takes ``college_id``
- ``testing``: in-memory adapters for every port, for tests only
"""

__version__ = "0.0.0"
