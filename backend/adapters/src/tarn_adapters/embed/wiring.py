"""The embedders: segmentation's (the configured sentence-transformers model, or the core's
model-free trigram embedder: ``TARN_EMBEDDING_MODEL=trigram``, or when the library of the
``ocr`` group is missing) and scoring's (``TARN_SCORING_EMBEDDING_MODEL``), which must run on
this machine: only a model id whose weights are already in the local cache, loaded without
contacting the hub, or the trigram embedder (design.md "Embedding model": no student text
leaves Tarn in the non-LLM phase). Models load on first use, on the CPU."""

import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path

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


MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")


class RemoteEmbedderError(ValueError):
    """The scoring embedder is configured as something that is not a local model."""


def model_cached(cache_dir: Path, model: str) -> bool:
    """Are the model's weights in the Hugging Face cache under ``cache_dir``?"""
    snapshots = cache_dir / f"models--{model.replace('/', '--')}" / "snapshots"
    return snapshots.is_dir() and any(snapshots.iterdir())


def build_scoring_embedder(settings: Settings) -> EmbedderSetup:
    """The scoring embedder. Refuses (``RemoteEmbedderError``) anything but ``trigram`` or a
    Hugging Face model id (``org/name``): no URL, no API, no other scheme. The model loads with
    ``local_files_only``; when its weights are not cached the trigram embedder stands in."""
    name = settings.scoring_embedding_model.strip()
    if name == TRIGRAM:
        return EmbedderSetup(TrigramEmbedder(), None)
    if not MODEL_ID.fullmatch(name):
        raise RemoteEmbedderError(
            "TARN_SCORING_EMBEDDING_MODEL must be 'trigram' or a local model id such as "
            "'sentence-transformers/all-MiniLM-L6-v2'"
        )
    if importlib.util.find_spec("sentence_transformers") is None:
        return EmbedderSetup(TrigramEmbedder(), "sentence-transformers is not installed")
    cache_dir = settings.model_dir / "huggingface"
    if not model_cached(cache_dir, name):
        return EmbedderSetup(
            TrigramEmbedder(), "model weights not fetched: run `tarn score models fetch`"
        )
    return EmbedderSetup(
        SentenceTransformerEmbedder(name, cache_dir=cache_dir, local_files_only=True), None
    )
