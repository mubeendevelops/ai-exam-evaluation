"""Pre-fill ground-truth pages from the OCR: clean a page, settle its orientation, find the
lines and read them with every engine; the selector's choice is the text the person starts from.

The pre-fill is a starting point and never truth: a line counts only once a person verified it
(``TruthStatus.VERIFIED``)."""

from collections.abc import Collection, Iterator
from pathlib import Path

from tarn_adapters.ocr.batch import page_ocr, read_pages
from tarn_adapters.ocr.images import clip
from tarn_adapters.ocr.wiring import OcrSetup
from tarn_core.domain.groundtruth import CaptureType, TruthPage
from tarn_core.domain.ocr import ContentClass
from tarn_core.services.groundtruth import GroundTruthService, PrefillLine
from tarn_core.services.ocr.selector import Lexicon


def prefill_file(
    path: Path,
    setup: OcrSetup,
    service: GroundTruthService,
    *,
    label: str,
    capture: CaptureType,
    pages: Collection[int],
    replace: bool = False,
    max_pages: int = 60,
) -> Iterator[TruthPage]:
    """Pre-fill the given pages (1-based) of a PDF or image: ``<label>-pNN`` in the set."""
    wanted = {number - 1 for number in pages}
    ocr = page_ocr(setup)
    lexicon = Lexicon(frozenset(), setup.word_list)
    for read in read_pages(
        path.read_bytes(), setup, ocr, lexicon, max_pages=max_pages, only=wanted
    ):
        width, height = setup.transform.size(read.image)
        lines = []
        for line in read.text.lines:
            box = clip(line.box, width, height)
            if box is None:
                continue
            chosen = line.choice.chosen if line.choice else None
            lines.append(
                PrefillLine(
                    box=box,
                    text="" if chosen is None else line.readings[chosen].text,
                    content_class=line.content_class or ContentClass.CURSIVE,
                )
            )
        if not lines:
            continue
        page_id = f"{label}-p{read.index + 1:02d}"
        yield service.add_prefilled(
            page_id=page_id,
            capture=capture,
            image=read.image,
            image_name=f"{page_id}.jpg",
            width=width,
            height=height,
            lines=lines,
            source=label,
            replace_prefill=replace,
        )
