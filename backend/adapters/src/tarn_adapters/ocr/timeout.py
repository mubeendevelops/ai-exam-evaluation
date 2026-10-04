"""A time limit around any OCR engine (design.md "Reliability": one engine down or slow, the
others carry on). The engine runs in a worker thread; past the limit the reader gets
``EngineTimeoutError``. A thread cannot be killed, so a local engine that overran finishes in
the background and its result is dropped; cloud SDKs get their own network timeouts too."""

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from tarn_core.domain.booklet import LineReading
from tarn_core.domain.common import Box, EngineRef
from tarn_core.errors import EngineTimeoutError
from tarn_core.ports.engines import OcrEngine


class TimedEngine:
    def __init__(self, engine: OcrEngine, seconds: float) -> None:
        self._engine = engine
        self._seconds = seconds
        # One thread per engine: a call that overran keeps it busy, and the next call waits for
        # it instead of piling up threads (so a stuck engine times out again, quickly).
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"ocr-{engine.ref.name}")

    @property
    def ref(self) -> EngineRef:
        return self._engine.ref

    @property
    def inner(self) -> OcrEngine:
        return self._engine

    def read(self, image: bytes, lines: Sequence[Box]) -> Sequence[LineReading]:
        future = self._pool.submit(self._engine.read, image, lines)
        try:
            return future.result(timeout=self._seconds)
        except FutureTimeout:
            future.cancel()
            raise EngineTimeoutError(
                f"{self._engine.ref.name} took longer than {self._seconds:g} s"
            ) from None
