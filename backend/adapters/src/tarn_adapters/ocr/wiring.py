"""Builds the OCR set-up from settings: the selector settings, the layout detector, every
configured engine that can run here (each behind a time limit), the page transform and the
word list. Engines that cannot run are left out with the reason, never silently.

Cloud engines (Textract, Azure Read, Document AI) run only when ``TARN_CLOUD_OCR_ENABLED`` is
set, their credentials are present, and, outside production,
``TARN_CLOUD_OCR_ALLOW_IN_DEVELOPMENT`` too: in development student data stays on this machine
(design decision 4)."""

import importlib.util
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from tarn_adapters.compute import DeviceInfo, detect_device
from tarn_adapters.config import Settings
from tarn_adapters.ocr import azure_read, documentai, paddle, tesseract, textract, trocr
from tarn_adapters.ocr.timeout import TimedEngine
from tarn_adapters.ocr.transform import OpenCvPageTransform
from tarn_adapters.ocr.words import FileWordList
from tarn_core.domain.ocr import ContentClass, SelectorSettings
from tarn_core.errors import EngineFailedError
from tarn_core.ports.engines import LayoutDetector, OcrEngine
from tarn_core.services.ocr.reader import OrientationPolicy

LOCAL_ENGINES = ("trocr", "paddle", "tesseract")
CLOUD_ENGINES = ("textract", "azure", "docai")


def engine_list(value: str) -> tuple[str, ...]:
    names = [n.strip().lower() for n in value.split(",")]
    return tuple(dict.fromkeys(n for n in names if n))


def selector_settings(settings: Settings) -> SelectorSettings:
    return SelectorSettings(
        alpha=settings.ocr_alpha,
        beta=settings.ocr_beta,
        flag_threshold=settings.ocr_flag_threshold,
        engine_sets={
            ContentClass.PRINT: engine_list(settings.ocr_engines_print),
            ContentClass.CURSIVE: engine_list(settings.ocr_engines_cursive),
            ContentClass.NUMERIC: engine_list(settings.ocr_engines_numeric),
        },
    )


def cloud_refusal(settings: Settings) -> str | None:
    """Why the cloud engines may not run here, or None when they may."""
    if not settings.cloud_ocr_enabled:
        return "cloud OCR is disabled (TARN_CLOUD_OCR_ENABLED=false)"
    if settings.env != "production" and not settings.cloud_ocr_allow_in_development:
        return (
            "outside production student data stays on this machine "
            "(TARN_CLOUD_OCR_ALLOW_IN_DEVELOPMENT=false)"
        )
    return None


def _aws_credentials_present() -> bool:
    try:
        import boto3
    except ImportError:
        return False
    return boto3.Session().get_credentials() is not None


@dataclass
class OcrSetup:
    settings: SelectorSettings
    layout: LayoutDetector
    engines: dict[str, OcrEngine]
    transform: OpenCvPageTransform
    word_list: FileWordList
    orientation: OrientationPolicy
    device: DeviceInfo
    skipped: dict[str, str] = field(default_factory=dict)
    """Configured engines left out, with the reason."""


type Factory = Callable[[Settings, DeviceInfo], OcrEngine]


def _require(*modules: str) -> None:
    """Refuse an engine whose libraries are missing (the `ocr` dependency group)."""
    missing = [m for m in modules if importlib.util.find_spec(m) is None]
    if missing:
        raise EngineFailedError(f"{', '.join(missing)} not installed (the `ocr` dependency group)")


def _trocr(settings: Settings, device: DeviceInfo) -> OcrEngine:
    _require("torch", "transformers")
    return trocr.TrOcrEngine(
        model=settings.trocr_model,
        device=device,
        batch=settings.trocr_batch,
        precision=settings.trocr_precision,
        cache_dir=settings.model_dir / "huggingface",
    )


def _paddle(settings: Settings, device: DeviceInfo) -> OcrEngine:
    _require("paddleocr")
    return paddle.PaddleOcrEngine(recognition_model=settings.ocr_recognition_model)


def _tesseract(settings: Settings, device: DeviceInfo) -> OcrEngine:
    return tesseract.TesseractEngine()


def _textract(settings: Settings, device: DeviceInfo) -> OcrEngine:
    if not _aws_credentials_present():
        raise EngineFailedError("no AWS credentials (or boto3 missing: --group cloud-ocr)")
    return textract.TextractEngine(
        textract.boto3_detect(settings.aws_region, settings.ocr_engine_timeout_seconds)
    )


def _azure(settings: Settings, device: DeviceInfo) -> OcrEngine:
    key = settings.azure_di_key.get_secret_value()
    if not settings.azure_di_endpoint or not key:
        raise EngineFailedError("TARN_AZURE_DI_ENDPOINT and TARN_AZURE_DI_KEY are not set")
    return azure_read.AzureReadEngine(
        azure_read.sdk_analyze(settings.azure_di_endpoint, key, settings.ocr_engine_timeout_seconds)
    )


def _docai(settings: Settings, device: DeviceInfo) -> OcrEngine:
    if not settings.docai_processor:
        raise EngineFailedError("TARN_DOCAI_PROCESSOR is not set")
    return documentai.DocumentAiEngine(
        documentai.sdk_process(settings.docai_processor, settings.ocr_engine_timeout_seconds),
        version=settings.docai_processor.rsplit("/", 1)[-1],
    )


FACTORIES: Mapping[str, Factory] = {
    "trocr": _trocr,
    "paddle": _paddle,
    "tesseract": _tesseract,
    "textract": _textract,
    "azure": _azure,
    "docai": _docai,
}


def _layout(settings: Settings) -> LayoutDetector:
    """The Paddle layout detector; without it nothing can be read, so its absence is an error
    for the whole OCR set-up, not a skipped engine."""
    _require("paddleocr")
    return paddle.PaddleLayoutDetector(
        detection_model=settings.ocr_detection_model,
        layout_model=settings.ocr_layout_model or None,
    )


def configure_model_dir(settings: Settings) -> None:
    """Model downloads go to the git-ignored model folder."""
    settings.model_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(settings.model_dir / "huggingface"))
    paddle.configure(settings.model_dir)


def build_ocr(  # raises EngineFailedError when the layout detector cannot run
    settings: Settings,
    *,
    device: DeviceInfo | None = None,
    factories: Mapping[str, Factory] = FACTORIES,
    layout: LayoutDetector | None = None,
) -> OcrSetup:
    configure_model_dir(settings)
    selector = selector_settings(settings)
    device = device or detect_device(settings.device)
    refusal = cloud_refusal(settings)
    engines: dict[str, OcrEngine] = {}
    skipped: dict[str, str] = {}
    for name in selector.all_engines:
        if name not in factories:
            skipped[name] = "unknown engine"
            continue
        if name in CLOUD_ENGINES and refusal is not None:
            skipped[name] = refusal
            continue
        try:
            engine = factories[name](settings, device)
        except EngineFailedError as error:
            skipped[name] = str(error)
            continue
        engines[name] = TimedEngine(engine, settings.ocr_engine_timeout_seconds)
    return OcrSetup(
        settings=selector,
        layout=layout or _layout(settings),
        engines=engines,
        transform=OpenCvPageTransform(),
        word_list=FileWordList.load(settings.english_words),
        orientation=OrientationPolicy(
            enabled=settings.ocr_orientation_check, margin=settings.ocr_orientation_margin
        ),
        device=device,
        skipped=skipped,
    )
