"""Stages with the image work faked and OCR scripted, for tests of every path that runs a booklet
(API, worker, ``tarn evaluate``): the pipeline is the real one, the engines are stand-ins."""

from collections.abc import Sequence
from uuid import UUID

from tarn_adapters.stages import OcrKit, Stages
from tarn_core.domain.booklet import RegionKind
from tarn_core.domain.ocr import SelectorSettings
from tarn_core.ids import CollegeId
from tarn_core.ports.engines import DetectedRegion
from tarn_core.ports.storage import BlobStore
from tarn_core.services.ocr.reader import OrientationPolicy
from tarn_core.services.pipeline import QualityPolicy
from tarn_core.services.segmentation.similarity import TrigramEmbedder
from tarn_core.testing import (
    FakePageCleaner,
    FakePageSplitter,
    FakePageTransform,
    ScriptedLayoutDetector,
    ScriptedOcrEngine,
)
from tarn_core.testing.segmentation import sheet

QP_CI_PAGE = (
    "^ Section B",
    "> 8. the indian constitution is a living document",
    "it is amended over time as the needs of people change",
    "> 9. parliamentary control instruments over the executive",
    "question hour, zero hour, adjournment and no confidence motions",
    "> 10. powers of the rajya sabha and the lok sabha",
    "the rajya sabha and lok sabha share legislative powers",
)
"""Three answers to the demo college's QP-CI, as the OCR lines of one page (the line syntax of
``tarn_core.testing.segmentation``)."""


def scripted_stages(blobs: BlobStore, lines: Sequence[str] = QP_CI_PAGE) -> Stages:
    """Fake splitter and cleaner, a layout detector that finds ``lines`` where
    ``tarn_core.testing.segmentation.sheet`` puts them, and an OCR engine that reads them back."""
    built = sheet(CollegeId(UUID(int=1)), 0, lines)
    return Stages(
        blobs=blobs,
        splitter=FakePageSplitter(),
        cleaner=FakePageCleaner(),
        policy=QualityPolicy(),
        max_pages=40,
        ocr=OcrKit(
            layout=ScriptedLayoutDetector(
                [DetectedRegion(kind=RegionKind.TEXT_LINE, box=r.box) for r in built.regions]
            ),
            engines={
                "trocr": ScriptedOcrEngine(
                    "trocr", [r.teacher_text or "" for r in built.regions], 0.9
                )
            },
            transform=FakePageTransform(),
            word_list=None,
            settings=SelectorSettings(),
            orientation=OrientationPolicy(enabled=False),
        ),
        embedder=TrigramEmbedder(),
        scoring_embedder=TrigramEmbedder(),
    )
