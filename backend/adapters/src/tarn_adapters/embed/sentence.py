"""Sentence embeddings with ``sentence-transformers`` (the ``ocr`` dependency group), on the
CPU: the texts are short and few (a booklet's answer openings and the paper's questions), and
the GPU stays with TrOCR (one model at a time on the development GPU, D13).

Default model ``sentence-transformers/all-MiniLM-L6-v2`` (``TARN_EMBEDDING_MODEL``); P13 picks
the scoring model by benchmark, and segmentation's similarity thresholds belong to the model
they were measured with. Vectors are L2-normalised. No text is logged."""

from collections.abc import Sequence
from functools import cached_property
from pathlib import Path
from typing import Any

from tarn_core.domain.common import EngineRef
from tarn_core.errors import EngineFailedError

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class SentenceTransformerEmbedder:
    def __init__(
        self, model: str = DEFAULT_MODEL, *, cache_dir: Path | None = None, device: str = "cpu"
    ) -> None:
        self._name = model
        self._cache_dir = cache_dir
        self._device = device

    @cached_property
    def _model(self) -> Any:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:  # the `ocr` group is not installed
            raise EngineFailedError("sentence-transformers is not installed") from error
        return SentenceTransformer(
            self._name,
            device=self._device,
            cache_folder=None if self._cache_dir is None else str(self._cache_dir),
        )

    @property
    def ref(self) -> EngineRef:
        return EngineRef(name=self._name.rsplit("/", 1)[-1], version=self._version)

    @cached_property
    def _version(self) -> str:
        import sentence_transformers

        return f"st-{sentence_transformers.__version__}"

    @property
    def dimension(self) -> int:
        return int(self._model.get_sentence_embedding_dimension())

    def embed(self, texts: Sequence[str]) -> Sequence[tuple[float, ...]]:
        if not texts:
            return []
        vectors = self._model.encode(
            list(texts), batch_size=32, normalize_embeddings=True, show_progress_bar=False
        )
        return [tuple(float(x) for x in v) for v in vectors]
