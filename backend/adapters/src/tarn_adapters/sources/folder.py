"""``PageSource`` for a local folder: the page images (JPEG, PNG) of one booklet in name order
(``page2`` before ``page10``), or one PDF. Other files in the folder (a manifest, notes) are
ignored, and so are sub-folders. The folder is one booklet, so the ids the port passes are not
used."""

from collections.abc import Sequence
from pathlib import Path

from tarn_adapters.sources.ordering import EXTENSIONS, media_type_of, natural_key
from tarn_core.errors import InvariantError, NotFoundError
from tarn_core.ids import BookletId, CollegeId
from tarn_core.ports.storage import PageImage


class FolderPageSource:
    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def pages(self, college_id: CollegeId, booklet_id: BookletId) -> Sequence[PageImage]:
        if not self._folder.is_dir():
            raise NotFoundError("the booklet folder")
        files = sorted(
            (
                p
                for p in self._folder.iterdir()
                if p.is_file() and p.suffix.lower() in EXTENSIONS and not p.name.startswith(".")
            ),
            key=lambda p: natural_key(p.name),
        )
        if not files:
            raise InvariantError("The folder holds no PDF, JPEG or PNG files.")
        pages = []
        for index, path in enumerate(files):
            data = path.read_bytes()
            pages.append(PageImage(index=index, data=data, media_type=media_type_of(data)))
        return pages
