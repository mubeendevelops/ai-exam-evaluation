"""The embedder segmentation uses: the configured sentence-transformers model, or the core's
model-free trigram embedder (``TARN_EMBEDDING_MODEL=trigram``, or when the library of the
``ocr`` group is missing). The model loads on first use, on the CPU."""

import importlib.util
from dataclasses import dataclass

from tarn_adapters.config import Settings
from tarn_adapters.embed.sentence import SentenceTransformerEmbedder
from tarn_core.ports.engines import Embedder
from tarn_core.services.segmentation.similarity import TrigramEmbedder

TRIGRAM = "trigram"


@dataclass(frozen=True, slots=True)
class EmbedderSetup:
    embedder: Embedder
    fallback_reason: str | None
    """Why the trigram embedder is used instead of the configured model (None: as configured)."""


def build_embedder(settings: Settings) -> EmbedderSetup:
    if settings.embedding_model == TRIGRAM:
        return EmbedderSetup(TrigramEmbedder(), None)
    if importlib.util.find_spec("sentence_transformers") is None:
        return EmbedderSetup(TrigramEmbedder(), "sentence-transformers is not installed")
    return EmbedderSetup(
        SentenceTransformerEmbedder(
            settings.embedding_model, cache_dir=settings.model_dir / "huggingface"
        ),
        None,
    )
