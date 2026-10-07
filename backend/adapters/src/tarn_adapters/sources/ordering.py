"""Shared by the page sources: reading order and the file types a booklet may hold."""

import re

from tarn_core.errors import UnsupportedFileError
from tarn_core.services.uploads import sniff_media_type

EXTENSIONS = frozenset({".pdf", ".jpg", ".jpeg", ".png"})
"""File names that are taken as booklet files when a folder or an archive holds other files
too (a manifest, notes). The content is still judged by its first bytes."""

_DIGITS = re.compile(r"(\d+)")


def natural_key(name: str) -> tuple[tuple[int, int | str], ...]:
    """Order ``page2`` before ``page10``: digits compare as numbers, text case-insensitively."""
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold()) for part in _DIGITS.split(name)
    )


def media_type_of(data: bytes) -> str:
    """``application/pdf``, ``image/jpeg`` or ``image/png`` from the first bytes."""
    try:
        return sniff_media_type(data)
    except UnsupportedFileError:
        raise UnsupportedFileError(
            "A booklet file must be a PDF, or a JPEG or PNG image."
        ) from None
