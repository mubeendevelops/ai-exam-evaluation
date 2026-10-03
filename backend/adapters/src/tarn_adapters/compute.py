"""Choose the compute device at run time: CUDA when available, otherwise the CPU.

The GPU is optional. The development laptop has a 4 GB GTX 1650, so callers load one model
at a time. ``torch`` is imported lazily: it is not installed until the OCR prompt (P10), and
without it the answer is always the CPU, with the reason recorded in ``detail``.
"""

import shutil
import subprocess
from dataclasses import dataclass
from typing import Literal

DeviceKind = Literal["cpu", "cuda"]
DevicePreference = Literal["auto", "cpu", "cuda"]


@dataclass(frozen=True)
class DeviceInfo:
    kind: DeviceKind
    name: str
    total_memory_mb: int | None
    detail: str

    @property
    def torch_device(self) -> str:
        return "cuda:0" if self.kind == "cuda" else "cpu"


CPU_ONLY = DeviceInfo("cpu", "cpu", None, "CPU selected")


def _torch_cuda() -> DeviceInfo | None:
    """The first CUDA device as seen by torch, or None when torch or CUDA is missing."""
    try:
        import torch
    except ImportError:
        return None
    if not torch.cuda.is_available():
        return None
    props = torch.cuda.get_device_properties(0)
    return DeviceInfo("cuda", props.name, props.total_memory // (1024 * 1024), "CUDA via torch")


def _nvidia_smi_gpu() -> str | None:
    """GPU name reported by the driver, or None. Used only to explain a CPU fallback."""
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return None
    try:
        out = subprocess.run(  # noqa: S603
            [exe, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    first = out.strip().splitlines()
    return first[0].strip() if first else None


def detect_device(preference: DevicePreference = "auto") -> DeviceInfo:
    """Resolve ``preference`` against what this machine or container can actually do.

    ``cuda`` raises when CUDA is unavailable (the caller asked for it explicitly);
    ``auto`` falls back to the CPU and says why.
    """
    if preference == "cpu":
        return CPU_ONLY

    cuda = _torch_cuda()
    if cuda is not None:
        return cuda
    if preference == "cuda":
        raise RuntimeError("TARN_DEVICE=cuda but torch cannot see a CUDA device")

    gpu = _nvidia_smi_gpu()
    if gpu is not None:
        return DeviceInfo("cpu", "cpu", None, f"GPU '{gpu}' present but torch+CUDA unavailable")
    return DeviceInfo("cpu", "cpu", None, "no CUDA device found")
