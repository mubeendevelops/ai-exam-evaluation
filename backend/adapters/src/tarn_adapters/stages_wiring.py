"""Build the booklet stages from the settings: the page cleaner and splitter, the OCR engines,
the segmentation and scoring embedders and the diagram recognizers. One place for the worker
and ``tarn evaluate``, so both read a booklet with the same engines.

Anything that cannot be set up is left out with a log line, never silently: no OCR leaves
``ocr=None`` (page cleaning still runs, reading fails visibly); a missing embedder falls back
to the model-free one; a missing diagram detector sends diagram criteria to the teacher."""

import structlog

from tarn_adapters.config import Settings
from tarn_adapters.embed.wiring import build_embedder, build_scoring_embedder
from tarn_adapters.imaging.cleaner import OpenCvPageCleaner
from tarn_adapters.imaging.pdf import PyMuPdfSplitter
from tarn_adapters.ocr.wiring import build_ocr
from tarn_adapters.stages import OcrKit, Stages
from tarn_core.errors import EngineFailedError
from tarn_core.ports.engines import SecondOpinionScorer
from tarn_core.ports.storage import BlobStore
from tarn_core.services.pipeline import QualityPolicy


def build_stages(
    settings: Settings,
    blobs: BlobStore,
    *,
    llm: SecondOpinionScorer | None = None,
    logger: str = "tarn_adapters.stages",
) -> Stages:
    log = structlog.get_logger(logger)
    kit: OcrKit | None = None
    try:
        ocr = build_ocr(settings)
    except EngineFailedError as error:
        # Page cleaning still runs; reading jobs fail visibly until OCR is available.
        log.error("ocr.unavailable", reason=str(error))
    else:
        log.info(
            "ocr.engines",
            engines=list(ocr.engines),
            skipped=ocr.skipped,
            device=ocr.device.kind,
            layout=f"{ocr.layout.ref.name} {ocr.layout.ref.version}",
        )
        if not ocr.engines:
            log.error("ocr.no_engines", skipped=ocr.skipped)
        kit = OcrKit(
            layout=ocr.layout,
            engines=ocr.engines,
            transform=ocr.transform,
            word_list=ocr.word_list,
            settings=ocr.settings,
            orientation=ocr.orientation,
        )

    embedding = build_embedder(settings)
    if embedding.fallback_reason is not None:
        log.warning("segmentation.embedder_fallback", reason=embedding.fallback_reason)
    log.info("segmentation.embedder", embedder=embedding.embedder.ref.name)
    scoring = build_scoring_embedder(settings)  # refuses a non-local embedder: fails start-up
    if scoring.fallback_reason is not None:
        log.warning("scoring.embedder_fallback", reason=scoring.fallback_reason)
    log.info("scoring.embedder", embedder=scoring.embedder.ref.name)
    from tarn_adapters.diagram.wiring import build_recognizers

    diagrams = build_recognizers(settings)
    if diagrams.skipped is not None:
        log.warning("diagram.recognizer_unavailable", reason=diagrams.skipped)
    else:
        refs = {r.ref for r in diagrams.recognizers.values()}
        log.info(
            "diagram.recognizers",
            kinds=sorted(k.value for k in diagrams.recognizers),
            recognizers=sorted(f"{r.name} {r.version}" for r in refs),
        )

    return Stages(
        blobs=blobs,
        splitter=PyMuPdfSplitter(),
        cleaner=OpenCvPageCleaner(
            max_edge_px=settings.page_max_edge_px, max_bytes=settings.page_max_bytes
        ),
        policy=QualityPolicy(
            min_sharpness=settings.quality_min_sharpness,
            max_glare_share=settings.quality_max_glare_share,
            min_page_edge_px=settings.quality_min_page_edge_px,
        ),
        max_pages=settings.upload_max_pages,
        ocr=kit,
        embedder=embedding.embedder,
        scoring_embedder=scoring.embedder,
        word_list=None if kit is None else kit.word_list,
        recognizers=diagrams.recognizers,
        llm=llm,
    )
