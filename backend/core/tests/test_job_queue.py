"""The in-memory job queue obeys the queue contract (the PostgreSQL queue is held to the same
functions in the adapters' integration tests)."""

import pytest

from tarn_core.testing import MemoryQueueHarness
from tarn_core.testing.queue_contract import ALL


@pytest.mark.parametrize("check", ALL, ids=[c.__name__ for c in ALL])
def test_memory_queue_contract(check) -> None:  # type: ignore[no-untyped-def]
    check(MemoryQueueHarness())
