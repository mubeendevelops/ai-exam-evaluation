"""``PageSource`` for a byte stream (stdin or a pipe), so a scanner or another program can feed
a booklet without a file system: ``cat booklet.pdf | tarn evaluate - …``.

The stream holds one file (a PDF, a JPEG or a PNG), or a tar archive of such files, which are
taken in name order. The stream is read once, up to ``max_bytes``; archive members are read in
memory and never written to disk, so a member's name cannot reach the file system."""

import io
import tarfile
from collections.abc import Sequence
from typing import BinaryIO

from tarn_adapters.sources.ordering import EXTENSIONS, media_type_of, natural_key
from tarn_core.errors import InvariantError, UnsupportedFileError, UploadTooLargeError
from tarn_core.ids import BookletId, CollegeId
from tarn_core.ports.storage import PageImage

CHUNK = 1024 * 1024
MAX_MEMBERS = 200


class StreamPageSource:
    def __init__(self, stream: BinaryIO, *, max_bytes: int = 100 * 1024 * 1024) -> None:
        self._stream = stream
        self._max_bytes = max_bytes
        self._pages: list[PageImage] | None = None

    def pages(self, college_id: CollegeId, booklet_id: BookletId) -> Sequence[PageImage]:
        if self._pages is None:
            self._pages = self._read()
        return self._pages

    def _read(self) -> list[PageImage]:
        buffer = io.BytesIO()
        while chunk := self._stream.read(CHUNK):
            buffer.write(chunk)
            if buffer.tell() > self._max_bytes:
                raise UploadTooLargeError(
                    f"The stream is larger than {self._max_bytes // (1024 * 1024)} MB."
                )
        data = buffer.getvalue()
        if not data:
            raise InvariantError("The stream is empty.")
        if _is_tar(data):
            return self._archive(data)
        return [PageImage(index=0, data=data, media_type=media_type_of(data))]

    def _archive(self, data: bytes) -> list[PageImage]:
        members: list[tuple[str, bytes]] = []
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
                for info in archive:
                    name = info.name.rsplit("/", 1)[-1]
                    if (
                        not info.isreg()
                        or name.startswith(".")
                        or not name.lower().endswith(tuple(EXTENSIONS))
                    ):
                        continue
                    if len(members) >= MAX_MEMBERS:
                        raise UploadTooLargeError("The archive holds too many files.")
                    handle = archive.extractfile(info)
                    if handle is not None:
                        members.append((name, handle.read()))
        except tarfile.TarError:
            raise UnsupportedFileError("The stream is not a readable tar archive.") from None
        if not members:
            raise InvariantError("The archive holds no PDF, JPEG or PNG files.")
        members.sort(key=lambda m: natural_key(m[0]))
        return [
            PageImage(index=i, data=content, media_type=media_type_of(content))
            for i, (_, content) in enumerate(members)
        ]


def _is_tar(data: bytes) -> bool:
    """The ``ustar`` magic at byte 257 (a PDF or an image never has it there)."""
    return data[257:262] == b"ustar"
