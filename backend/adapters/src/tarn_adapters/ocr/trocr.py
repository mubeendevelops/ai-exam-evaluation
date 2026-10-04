"""TrOCR (``microsoft/trocr-base-handwritten`` by default): the main local handwriting engine.

Reads the detected line crops in small batches with greedy decoding and the decoder's key/value
cache (the model's own generation config turns the cache off, which made a page 3× slower).
Confidence = exp(mean log-probability of the generated tokens).

On CUDA the model holds the process-wide GPU slot (one model at a time on the 4 GB development
GPU); when CUDA runs out of memory the batch is halved, and at one line per batch the model moves
to the CPU for good. Precision (``TARN_TROCR_PRECISION``): ``auto`` times one matrix product in
fp16 and fp32 on the device and takes the faster. GPUs without tensor cores are slower in fp16:
on the development GTX 1650, fp16 measured 0.31 TFLOPS against 1.05 TFLOPS for fp32.

Measured on that GPU (P10), one sample page of 22 lines: fp16 without cache 31 s, fp32 with
cache 2.7 s; base fp32 peaks at about 1.8 GB. Base is the default until the benchmark (P11)
compares it with ``trocr-large-handwritten``."""

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Literal

from tarn_adapters.compute import GPU_SLOT, DeviceInfo, ModelSlot
from tarn_adapters.ocr.images import crop, decode
from tarn_core.domain.booklet import LineReading
from tarn_core.domain.common import Box, EngineRef
from tarn_core.errors import EngineFailedError

type Precision = Literal["auto", "fp16", "fp32"]

NAME = "trocr"
MAX_NEW_TOKENS = 96


@dataclass
class _Loaded:
    processor: Any
    model: Any
    device: str
    dtype: Any


def _torch() -> Any:
    try:
        import torch
    except ImportError as error:  # pragma: no cover
        raise EngineFailedError("torch is not installed") from error
    return torch


def is_out_of_memory(error: BaseException) -> bool:
    return type(error).__name__ == "OutOfMemoryError" or "out of memory" in str(error).lower()


class TrOcrEngine:
    def __init__(
        self,
        *,
        model: str,
        device: DeviceInfo,
        batch: int = 0,
        cache_dir: Path | None = None,
        precision: Precision = "auto",
        slot: ModelSlot = GPU_SLOT,
        loader: Callable[[str, str, Path | None, str], _Loaded] | None = None,
    ) -> None:
        self.ref = EngineRef(name=NAME, version=model)
        self._model_id = model
        self._device = device.torch_device
        self._batch = batch or (8 if device.kind == "cuda" else 4)
        self._cache_dir = cache_dir
        self._slot = slot
        self._loader = loader or _load
        self._precision: Precision = precision
        self._cpu_model: _Loaded | None = None
        self.fallbacks: list[str] = []
        """What went wrong on the way (out of memory → smaller batch → CPU), for the logs."""

    @property
    def device(self) -> str:
        return self._device

    @property
    def batch(self) -> int:
        return self._batch

    def _model(self) -> _Loaded:
        if self._device == "cpu":
            if self._cpu_model is None:
                self._cpu_model = self._loader(self._model_id, "cpu", self._cache_dir, "fp32")
            return self._cpu_model
        precision = resolve_precision(self._device, self._precision)
        return self._slot.acquire(
            f"{NAME}:{self._model_id}:{precision}",
            lambda: self._loader(self._model_id, self._device, self._cache_dir, precision),
            _release,
        )

    def read(self, image: bytes, lines: Sequence[Box]) -> Sequence[LineReading]:
        pixels = decode(image)
        crops: list[tuple[Box, Any]] = []
        for box in lines:
            piece = crop(pixels, box, pad=4)
            if piece is not None:
                crops.append((box, piece[:, :, ::-1]))  # BGR → RGB
        readings: list[LineReading] = []
        start = 0
        while start < len(crops):
            chunk = crops[start : start + self._batch]
            try:
                texts = self._recognise([c for _, c in chunk])
            except Exception as error:
                if not is_out_of_memory(error) or self._device == "cpu":
                    raise
                self._shrink()
                continue
            for (box, _), (text, confidence) in zip(chunk, texts, strict=True):
                readings.append(
                    LineReading(engine=self.ref, text=text, box=box, confidence=confidence)
                )
            start += len(chunk)
        return readings

    def _shrink(self) -> None:
        torch = _torch()
        torch.cuda.empty_cache()
        if self._batch > 1:
            self._batch //= 2
            self.fallbacks.append(f"out of memory: batch {self._batch}")
            return
        self._slot.free()
        self._device = "cpu"
        self.fallbacks.append("out of memory at batch 1: CPU")

    def _recognise(self, crops: list[Any]) -> list[tuple[str, float]]:
        loaded = self._model()
        torch = _torch()
        inputs = loaded.processor(images=crops, return_tensors="pt").pixel_values
        inputs = inputs.to(loaded.device, dtype=loaded.dtype)
        with torch.inference_mode():
            out = loaded.model.generate(
                inputs,
                max_new_tokens=MAX_NEW_TOKENS,
                # Greedy: the token scores are then the chosen tokens' own probabilities (beam
                # search would need beam indices to read them back), and it is faster.
                num_beams=1,
                do_sample=False,
                use_cache=True,
                output_scores=True,
                return_dict_in_generate=True,
            )
        texts = loaded.processor.batch_decode(out.sequences, skip_special_tokens=True)
        scores = loaded.model.compute_transition_scores(
            out.sequences, out.scores, normalize_logits=True
        )
        pad_id = loaded.processor.tokenizer.pad_token_id
        result = []
        for i, text in enumerate(texts):
            # Padding after the end of a shorter line is not part of it; the end-of-text token
            # is (its probability says the line ended where it did).
            logs = [
                float(s)
                for t, s in zip(out.sequences[i, 1:].tolist(), scores[i].tolist(), strict=False)
                if t != pad_id and math.isfinite(float(s))
            ]
            confidence = math.exp(sum(logs) / len(logs)) if logs else 0.0
            result.append((text.strip(), min(1.0, max(0.0, confidence))))
        return result


def resolve_precision(device: str, precision: Precision) -> str:
    """fp32 on the CPU; on CUDA the configured precision, or for ``auto`` the faster one."""
    if not device.startswith("cuda"):
        return "fp32"
    if precision != "auto":
        return precision
    return "fp16" if _fp16_is_faster(device) else "fp32"


@cache
def _fp16_is_faster(device: str) -> bool:
    torch = _torch()
    seconds = {}
    for dtype in (torch.float16, torch.float32):
        a = torch.randn(1024, 1024, device=device, dtype=dtype)
        a @ a  # warm-up (kernel selection)
        torch.cuda.synchronize(device)
        started = time.perf_counter()
        for _ in range(10):
            a @ a
        torch.cuda.synchronize(device)
        seconds[dtype] = time.perf_counter() - started
    return bool(seconds[torch.float16] < seconds[torch.float32])


def _load(model_id: str, device: str, cache_dir: Path | None, precision: str) -> _Loaded:
    torch = _torch()
    try:
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel
    except ImportError as error:  # pragma: no cover
        raise EngineFailedError("transformers is not installed") from error
    dtype = torch.float16 if precision == "fp16" else torch.float32
    cache = str(cache_dir) if cache_dir is not None else None
    processor = TrOCRProcessor.from_pretrained(model_id, cache_dir=cache)
    model = VisionEncoderDecoderModel.from_pretrained(model_id, cache_dir=cache, dtype=dtype)
    model = model.to(device)
    model.eval()
    return _Loaded(processor=processor, model=model, device=device, dtype=dtype)


def _release(loaded: _Loaded) -> None:
    loaded.model.to("cpu")
    del loaded.model
    torch = _torch()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
