"""``PageSource`` adapters: where a booklet's files come from (R1, R2).

- ``FolderPageSource``: a local folder of page images, or one PDF.
- ``StreamPageSource``: a byte stream (stdin or a pipe): one file, or a tar archive of files.
- ``GcsEventPageSource`` (``gcs_event``): the objects behind a Cloud Storage "finalized" event.

Each returns the booklet's *source files* in reading order; the upload service judges them by
their first bytes and the page pipeline splits and cleans them."""

from tarn_adapters.sources.folder import FolderPageSource
from tarn_adapters.sources.stream import StreamPageSource

__all__ = ["FolderPageSource", "StreamPageSource"]
