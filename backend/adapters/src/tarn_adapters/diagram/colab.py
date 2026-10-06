"""Training entry point for a cloud GPU notebook (``scripts/colab/train_diagram_detector.ipynb``):
``python -m tarn_adapters.diagram.colab --data DATA --out OUT [options]``. It needs only the
bundle's code, ``torch``, ``transformers``, OpenCV and NumPy (no Tarn settings, database or
stack). ``OUT`` should be on persistent storage (Google Drive): the checkpoint after every
epoch lets a disconnected session resume with the same command."""

import argparse
import sys
from pathlib import Path

from tarn_adapters.diagram.train import BASE_MODEL, TrainConfig, train


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Fine-tune the Tarn shape-and-arrow detector.")
    p.add_argument("--data", type=Path, required=True, help="Folder with manifest.jsonl")
    p.add_argument("--out", type=Path, required=True, help="Model folder (persistent)")
    p.add_argument("--epochs", type=int, default=40)
    p.add_argument("--steps", type=int, default=500, help="Optimiser steps per epoch")
    p.add_argument("--batch", type=int, default=8)
    p.add_argument("--accumulate", type=int, default=1)
    p.add_argument("--size", type=int, default=1024)
    p.add_argument("--lr", type=float, default=1e-4)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--version", default="1")
    p.add_argument("--base", default=BASE_MODEL)
    p.add_argument("--no-amp", action="store_true", help="Train in fp32")
    a = p.parse_args(argv)

    def log(line: str) -> None:
        print(line, flush=True)  # noqa: T201  (notebook output)

    cfg = TrainConfig(
        manifests=[a.data / "manifest.jsonl"],
        out=a.out,
        base=a.base,
        size=a.size,
        batch=a.batch,
        accumulate=a.accumulate,
        epochs=a.epochs,
        steps=a.steps,
        lr=a.lr,
        version=a.version,
        amp=not a.no_amp,
        workers=a.workers,
    )
    log(f"published {train(cfg, log=log)}")


if __name__ == "__main__":
    main(sys.argv[1:])
