"""The English word list of the selector's lexicon term.

The bundled list (``data/english-words.txt.gz``) is SCOWL size 50 (en_US + en_GB, words of two
or more letters, lower-cased), built by ``scripts/build_word_list.sh``; SCOWL's licence permits
redistribution (see the header of that script)."""

import gzip
from importlib.resources import files
from pathlib import Path


class FileWordList:
    def __init__(self, words: frozenset[str]) -> None:
        self._words = words

    @classmethod
    def load(cls, path: Path | None = None) -> "FileWordList":
        """One word per line, plain or gzip; ``None`` = the bundled list."""
        if path is None:
            raw = files("tarn_adapters.ocr").joinpath("data/english-words.txt.gz").read_bytes()
            text = gzip.decompress(raw).decode("utf-8")
        elif path.suffix == ".gz":
            text = gzip.decompress(path.read_bytes()).decode("utf-8")
        else:
            text = path.read_text(encoding="utf-8")
        return cls(frozenset(w.strip().casefold() for w in text.splitlines() if w.strip()))

    def __len__(self) -> int:
        return len(self._words)

    def contains(self, word: str) -> bool:
        word = word.casefold()
        if word in self._words:
            return True
        # Possessives and typographic apostrophes: "network's" counts as "network".
        for suffix in ("'s", "’s"):
            if word.endswith(suffix) and word[: -len(suffix)] in self._words:
                return True
        return False
