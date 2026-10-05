"""The text an answer is scored on, from its segments' regions in reading order.

Left out: struck-out lines (design.md "Reliability"), diagram and table containers (their cells
are kept: they are text lines), and written labels; the first line of each segment loses its
answer label ("12 b)", "Ans:"), so the question number is not read as a numeric answer. The
teacher's text wins over the OCR reading (``Region.text``).

A question answered twice (D91: two segments flagged ``duplicate``) gives one candidate text per
copy; the scoring service scores each and suggests the higher (D98)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from tarn_core.domain.booklet import Region, RegionKind, Segment, SegmentFlag
from tarn_core.ids import RegionId
from tarn_core.services.scoring.text import split_sentences, strip_label, word_count


class HasRegions(Protocol):
    """A stored segment or a proposed one (the calibration set reads the segmenter's)."""

    @property
    def region_ids(self) -> tuple[RegionId, ...]: ...


_SKIPPED_KINDS = frozenset({RegionKind.DIAGRAM, RegionKind.TABLE, RegionKind.LABEL})


@dataclass(frozen=True, slots=True, kw_only=True)
class AnswerText:
    sentences: tuple[str, ...]
    lines: int
    """Lines of text used."""
    low_ocr_lines: int
    """Of those, lines whose best OCR reading was below threshold (and not corrected)."""
    struck_out: int
    """Lines left out because they were struck out."""

    @property
    def text(self) -> str:
        return "\n".join(self.sentences)

    @property
    def words(self) -> int:
        return word_count(self.sentences)

    def low_ocr_share(self) -> float:
        return self.low_ocr_lines / self.lines if self.lines else 0.0

    @classmethod
    def from_text(cls, text: str) -> "AnswerText":
        """Plain text (tests, calibration sets): every line counts as well read."""
        lines = [line for line in text.splitlines() if line.strip()]
        return cls(
            sentences=tuple(split_sentences(lines)), lines=len(lines), low_ocr_lines=0, struck_out=0
        )


def segment_text(segments: Sequence[HasRegions], regions: Mapping[RegionId, Region]) -> AnswerText:
    lines: list[str] = []
    low = struck = 0
    for segment in segments:
        first = True
        for region_id in segment.region_ids:
            region = regions.get(region_id)
            if region is None or region.kind in _SKIPPED_KINDS:
                continue
            text = region.text
            if text is None or not text.strip():
                continue
            if region.struck_out:
                struck += 1
                continue
            if first:
                text = strip_label(text)
                first = False
                if not text.strip():
                    continue
            lines.append(text)
            if region.flagged and region.teacher_text is None:
                low += 1
        lines.append("")  # a segment ends a sentence
    return AnswerText(
        sentences=tuple(split_sentences(lines)),
        lines=sum(1 for line in lines if line),
        low_ocr_lines=low,
        struck_out=struck,
    )


def candidate_texts(
    segments: Sequence[Segment], regions: Mapping[RegionId, Region]
) -> list[AnswerText]:
    """One text for the answer, or one per copy when the question was answered twice. A
    segment not flagged ``duplicate`` (a continuation) joins the copy written before it."""
    ordered = sorted(segments, key=lambda s: s.position)
    duplicates = [s for s in ordered if SegmentFlag.DUPLICATE in s.flags]
    if len(duplicates) < 2:
        return [segment_text(ordered, regions)]
    groups: list[list[Segment]] = []
    for segment in ordered:
        if SegmentFlag.DUPLICATE in segment.flags or not groups:
            groups.append([segment])
        else:
            groups[-1].append(segment)
    return [segment_text(group, regions) for group in groups]
