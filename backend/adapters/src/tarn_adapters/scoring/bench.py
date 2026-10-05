"""The scoring-model benchmark (``tarn score bench``) and calibration (``tarn score
calibrate``) over a calibration set. Every model runs on this machine, from the local cache.

Per model: rank correlation between the answers' semantic similarity and the teacher's mark
(as a share of the question's marks), the mean absolute difference after fitting the bands
with questions held out (grouped cross-validation), the in-sample difference, and the time per
answer. Numbers and question codes only reach the report."""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from tarn_adapters.embed.sentence import SentenceTransformerEmbedder
from tarn_adapters.embed.wiring import MODEL_ID, TRIGRAM, RemoteEmbedderError, model_cached
from tarn_adapters.scoring.sets import CalibrationSet
from tarn_core.ports.engines import Embedder, WordList
from tarn_core.services.scoring.calibration import (
    Baseline,
    Features,
    ModelResult,
    balanced_mae,
    best_constant,
    cross_validated,
    features,
    fit_bands,
    semantic_signal,
    spearman,
)
from tarn_core.services.segmentation.similarity import TrigramEmbedder

CANDIDATES = (
    "sentence-transformers/all-MiniLM-L6-v2",
    "sentence-transformers/all-MiniLM-L12-v2",
    "sentence-transformers/all-mpnet-base-v2",
    "BAAI/bge-small-en-v1.5",
    "intfloat/e5-small-v2",
    "thenlper/gte-small",
    TRIGRAM,
)
"""Small open English sentence models that run on a CPU, and the model-free baseline."""


def local_embedder(name: str, cache_dir: Path) -> Embedder:
    """``trigram`` or a cached model loaded from local files only."""
    if name == TRIGRAM:
        return TrigramEmbedder()
    if not MODEL_ID.fullmatch(name):
        raise RemoteEmbedderError(f"not a local model id: {name!r}")
    if not model_cached(cache_dir, name):
        raise FileNotFoundError(f"{name}: weights not fetched (tarn score models fetch {name})")
    return SentenceTransformerEmbedder(name, cache_dir=cache_dir, local_files_only=True)


@dataclass(frozen=True, slots=True)
class Measured:
    result: ModelResult
    items: list[Features]


def measure(
    name: str,
    embedder: Embedder,
    data: CalibrationSet,
    *,
    word_list: WordList | None = None,
) -> Measured:
    embedder.embed(["warm up"])  # load the model before timing
    started = time.perf_counter()
    items = features(data.questions, data.answers, embedder, word_list=word_list)
    seconds = (time.perf_counter() - started) / max(1, len(items))
    with_semantic = [f for f in items if f.semantic]
    rho = spearman(
        [semantic_signal(f) for f in with_semantic],
        [float(f.teacher_mark / f.max_marks) for f in with_semantic],
    )
    policy = fit_bands(items)
    cv_mae, cv_balanced = cross_validated(items)
    return Measured(
        result=ModelResult(
            model=name,
            answers=len(items),
            spearman=rho,
            cv_balanced=cv_balanced,
            cv_mae=cv_mae,
            fitted_balanced=balanced_mae(items, policy),
            seconds_per_answer=seconds,
            policy=policy,
        ),
        items=items,
    )


def bench(
    models: Sequence[str],
    data: CalibrationSet,
    cache_dir: Path,
    progress: Callable[[str], None],
    *,
    word_list: WordList | None = None,
) -> tuple[list[ModelResult], tuple[Baseline, Baseline] | None]:
    """The results per model and the best constant guesses by plain and balanced difference
    (from the first model's features: a guess needs only the marks)."""
    results = []
    baseline: tuple[Baseline, Baseline] | None = None
    for name in models:
        try:
            embedder = local_embedder(name, cache_dir)
            measured = measure(name, embedder, data, word_list=word_list)
        except (FileNotFoundError, RemoteEmbedderError) as error:
            progress(f"{name}: skipped ({error})")
            continue
        if baseline is None:
            baseline = (
                best_constant(measured.items),
                best_constant(measured.items, balanced=True),
            )
        r = measured.result
        progress(
            f"{name}: rho {r.spearman:.3f}, balanced MAE cv {r.cv_balanced:.3f}, "
            f"MAE cv {r.cv_mae:.3f}, "
            f"{r.seconds_per_answer:.3f} s/answer"
        )
        results.append(r)
    return results, baseline
