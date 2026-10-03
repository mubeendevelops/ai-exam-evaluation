"""The API on the in-memory adapters: no database needed."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tarn_adapters.config import Settings
from tarn_api.app import create_app
from tarn_api.testing import MemoryBackends
from tarn_core.testing.auth_world import AuthWorld


@pytest.fixture
def backends() -> MemoryBackends:
    return MemoryBackends()


@pytest.fixture
def world(backends: MemoryBackends) -> AuthWorld:
    return AuthWorld(backends.mem)


@pytest.fixture
def client(backends: MemoryBackends) -> Iterator[TestClient]:
    app = create_app(Settings(_env_file=None, device="cpu"), backends=backends)
    with TestClient(app) as test_client:
        yield test_client
