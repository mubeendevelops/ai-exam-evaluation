"""core/paddle_workarounds.py — the single home for the enable_mkldnn=False
guard every PaddleX-backed model construction in this repo needs.

THE BUG: on this repo's pinned `paddlepaddle==3.3.1` (see requirements.txt),
constructing any PaddleX inference model (`TextDetection`, `TextRecognition`,
`LayoutDetection` — all of `paddleocr`'s `_models/` are PaddleX predictors
underneath) with its default oneDNN path enabled raises, on the FIRST
`.predict()` call, not at construction time:

    NotImplementedError: (Unimplemented) ConvertPirAttribute2RuntimeAttribute
    not support [pir::ArrayAttribute<pir::DoubleAttribute>]
    (at .../onednn_instruction.cc:116)

Passing `enable_mkldnn=False` to the constructor avoids it entirely — verified
2026-09-06 against `PP-OCRv5_server_det` (TextDetection) and
`PP-DocLayout_plus-L` (LayoutDetection) on the exact pinned version, both
crashing identically with `enable_mkldnn=True` (the library default) and both
clean with `enable_mkldnn=False`. This was previously fixed in two places
independently — core/ocr_engines/paddleocr_engine.py (TextDetection,
TextRecognition) and core/booklet_segmenter.py (LayoutDetection) — with a
copy-pasted `enable_mkldnn=False` and a copy-pasted `_quiet_paddle()` context
manager in each. This module is the merge of both; there is no third copy
anywhere else in the repo (core/table_extractor.py's docstring MENTIONS this
same crash — it is explaining why the REJECTED PP-StructureV3 alternative
needs the identical guard, per CLAUDE_CONTEXT.md §7B's benchmark table — but
that module never constructs a Paddle model itself, so it has nothing to
import from here).

THIS IS MEASURED AGAINST paddlepaddle==3.3.1 SPECIFICALLY. If that pin ever
moves, re-run this file's crash check before trusting the fix still applies:
construct any of the three models above with `enable_mkldnn=True` and call
`.predict()` on a real array; if it no longer raises, the guard may be
removable — but do not remove it on a hunch. Removing it requires re-running
all three benchmarks that measure the pipelines this guard sits inside:
`scripts/benchmark_ocr_engines.py`, `scripts/benchmark_diagram_extraction.py`,
and `scripts/benchmark_booklet_segmentation.py` — a paddlepaddle upgrade could
plausibly fix this crash while quietly changing model outputs, and those are
the only numbers in this repo that would catch it.

LIFECYCLE — unchanged by this module. Each call site still owns its own
single lazily-loaded model instance (`PaddleOCREngine._detector`/
`_recognizer` per instance, `core/booklet_segmenter.py`'s module-level
`_LAYOUT_MODEL`) — this module only centralizes HOW a model gets built, not
WHEN or how many get cached. Neither PaddleOCR nor sentence-transformers
documents thread-safety (CLAUDE_CONTEXT.md §7D), so that one-instance-per-
plugin shape stays exactly as it was.
"""
from __future__ import annotations

import contextlib
import io
import logging
import os
import warnings

# The guard itself. A plain module constant, not a function argument, so
# there is exactly one place that can be wrong.
ENABLE_MKLDNN = False

# PaddleX's model registry otherwise tries to reach a remote source-check
# endpoint on first construction; every call site needs this set before
# constructing any model, so it lives here rather than being repeated too.
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


@contextlib.contextmanager
def quiet_paddle():
    """Suppresses PaddleOCR/paddlex/paddle's own console chatter (a "No
    ccache found" UserWarning plus assorted INFO/WARNING logging) during
    model construction and inference, so a region/label report stays
    readable. Merge of the two previously-separate, near-identical
    `_quiet_paddle()` implementations in core/ocr_engines/paddleocr_engine.py
    and core/booklet_segmenter.py — this is a strict superset of both
    (CRITICAL-level log suppression covers the WARNING-level the segmenter
    used, and the ccache warning filter covers what the OCR engine used)."""
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning, message="No ccache found")
        logging.disable(logging.CRITICAL)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                yield
        finally:
            logging.disable(logging.NOTSET)


def construct(model_cls, **kwargs):
    """Constructs a PaddleX model (TextDetection, TextRecognition,
    LayoutDetection, ...) with the enable_mkldnn guard applied and paddle's
    construction-time chatter suppressed. Callers still do their own lazy
    caching (see module docstring) — this only replaces the repeated
    `with _quiet_paddle(): SomeModel(..., enable_mkldnn=False)` pattern.

        self._detector = construct(TextDetection, model_name=_DET_MODEL_NAME, ...)
    """
    with quiet_paddle():
        return model_cls(enable_mkldnn=ENABLE_MKLDNN, **kwargs)
