"""A ground-truth set in a folder: ``<page id>.jsonl`` and the page image next to it.

Student data: keep the folder under ``var/`` (git-ignored). Files are written atomically and, where
the file system allows, readable by the owner only."""

import contextlib
import os
import tempfile
from pathlib import Path

from tarn_core.domain.groundtruth import TruthPage
from tarn_core.errors import InvariantError, NotFoundError
from tarn_core.services.groundtruth import page_from_jsonl, page_to_jsonl


class DirectoryGroundTruthStore:
    def __init__(self, directory: Path) -> None:
        self._dir = directory

    @property
    def directory(self) -> Path:
        return self._dir

    def page_ids(self) -> list[str]:
        if not self._dir.is_dir():
            return []
        return sorted(p.stem for p in self._dir.glob("*.jsonl") if p.stem != "manifest")

    def get(self, page_id: str) -> TruthPage:
        path = self._dir / f"{page_id}.jsonl"
        if "/" in page_id or not path.is_file():
            raise NotFoundError(f"page {page_id} is not in the set")
        page = page_from_jsonl(path.read_text(encoding="utf-8"))
        if page.id != page_id:
            raise InvariantError(f"{path.name} holds page {page.id}")
        return page

    def image(self, page_id: str) -> bytes:
        page = self.get(page_id)
        path = self._dir / page.image
        if not path.is_file():
            raise NotFoundError(f"the image of page {page_id} is missing")
        return path.read_bytes()

    def save(self, page: TruthPage, image: bytes | None = None) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        if image is not None:
            self._write(self._dir / page.image, image)
        elif not (self._dir / page.image).is_file():
            raise InvariantError("the first save of a page needs its image")
        self._write(self._dir / f"{page.id}.jsonl", page_to_jsonl(page).encode("utf-8"))

    def _write(self, path: Path, data: bytes) -> None:
        handle, name = tempfile.mkstemp(dir=self._dir, prefix=".tmp-")
        temporary = Path(name)
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(data)
            with contextlib.suppress(OSError):  # not every file system keeps modes (NTFS)
                temporary.chmod(0o600)
            temporary.replace(path)
        except BaseException:
            with contextlib.suppress(OSError):
                temporary.unlink()
            raise
