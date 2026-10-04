"""Splitting a read booklet into answers (design.md "Segmentation"; C10, C12, C19).

Rules first, a similarity check second, the teacher last. The OCR misreads many labels (P11:
~23 % character error; "10." read as "to .", "2." as "a"), so no single line decides; the
booklet is cut wherever an answer *could* start and every piece is then given a question by
weighing all the evidence at once:

1. **Page order**: upload order, corrected by written page numbers (``pages``).
2. **Blocks**: the lines in reading order are cut at every possible answer start: a label at
   the left margin (``labels``: ``1.``, ``Q no. 3``, ``Ans:-``, ``12 b``, ``(ii)``), a line
   hanging left of the text column (labels sit in the margin), the top of a page, a large
   vertical gap. Section headings (``Section B``) set the section of the blocks after them.
3. **Decoding** (Viterbi over the blocks): each block is given a question leaf, the unassigned
   tray, or (only before the first answer) "before the first answer". A block scores for a
   leaf by its label (when the blueprint has that question, in the section being answered: a
   sub-part only inside a question that has it), by how much more its text resembles that
   question than the others (embedding similarity, ``Embedder`` port), and minus a penalty
   when the leaf is not in the section the student is in. Staying with the previous block's
   question is free (a **continuation**, rule 3: text above the first label of a page joins the
   previous answer); changing question costs ``switch_cost``, paid back by the block's start
   evidence (a label, a hanging line, a page top, a gap). A bare number that is really a point
   of a list inside an answer therefore stays in that answer unless its text says otherwise.
4. Neighbouring blocks with the same choice form one **segment**. A segment whose best
   question does not reach the threshold is unassigned, with that question proposed.
5. **Order does not matter**: segments are kept in written order (``position``) with the
   question they answer; a question answered twice keeps both, flagged ``duplicate``.
6. **Diagrams and tables** join the segment whose lines they sit among.

A paper with a single answerable question takes the whole booklet as its answer.

Everything here is pure: lines and boxes in, proposed segments out (``service`` stores them)."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from tarn_core.domain.blueprint import ExamBlueprint, QuestionSlot, leaf_label
from tarn_core.domain.booklet import Region, SegmentFlag, SegmentSource, SegmentSpan
from tarn_core.domain.common import Box, EngineRef
from tarn_core.ids import PageId, RegionId
from tarn_core.ports.engines import Embedder
from tarn_core.services.segmentation.labels import (
    Label,
    LabelKind,
    looks_like_mark,
    parse_label,
)
from tarn_core.services.segmentation.lines import Line, PageInput, PageLines, page_lines
from tarn_core.services.segmentation.pages import PageOrder, page_number_line, page_order
from tarn_core.services.segmentation.similarity import Vector, cosine


@dataclass(frozen=True, slots=True, kw_only=True)
class SegmentationPolicy:
    """Weights and thresholds; placeholders fitted on the few labelled sample booklets (P12
    report) until a teacher labels more."""

    label_margin: float = 0.04
    """A label may start up to this share of the page width right of the body's left edge."""
    outdent: float = 0.06
    """A line starting this share of the page width left of the body hangs in the margin (full
    ``start_outdent`` evidence); from ``min_outdent`` on, the evidence grows with the hang."""
    min_outdent: float = 0.02
    gap_factor: float = 2.2
    """A vertical gap above a line larger than this many line pitches starts a block."""
    block_chars: int = 600
    """Text of a block compared with the questions (its first characters)."""
    similarity_weight: float = 10.0
    """Weight of (similarity to a question − mean similarity to all questions)."""
    label_weight: float = 3.0
    strong_label_weight: float = 4.0
    section_penalty: float = 3.0
    switch_cost: float = 3.0
    start_label: float = 2.5
    start_mark: float = 1.5
    start_outdent: float = 1.5
    start_page_top: float = 1.0
    start_gap: float = 0.8
    unassigned_score: float = 0.5
    """What the unassigned tray scores for a block: a leaf must do better to be chosen."""
    preamble_score: float = 1.5
    """What "before the first answer" scores for an unlabelled block (cover pages and the
    name/USN lines resemble the paper's wording: the course name is on them)."""
    repeat_penalty: float = 4.0
    """Cost of starting a question already answered earlier (it stays possible: a question
    answered twice is kept and flagged)."""
    beam: int = 64
    similarity_threshold: float = 0.25
    """A segment chosen by similarity alone (no label agreeing) needs at least this raw
    similarity to its question, else it goes to the tray with that question proposed."""
    page_number_share: float = 0.4
    list_run: int = 3
    list_item_lines: int = 2


@dataclass(frozen=True, slots=True, kw_only=True)
class Leaf:
    label: str
    """``"7"`` or ``"12.a"``: what ``Answer.slot_label`` holds."""
    slot: str
    part: str | None
    section: str
    text: str
    """The question's wording, for similarity."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ProposedSegment:
    slot_label: str | None
    source: SegmentSource
    region_ids: tuple[RegionId, ...]
    spans: tuple[SegmentSpan, ...]
    position: int
    match_score: float | None = None
    flags: tuple[SegmentFlag, ...] = ()
    proposed_label: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SegmentationResult:
    order: PageOrder
    segments: tuple[ProposedSegment, ...]
    slivers: int
    """Lines left out as slivers of a neighbouring page."""
    embedder: EngineRef

    @property
    def written_order(self) -> tuple[str | None, ...]:
        return tuple(s.slot_label for s in self.segments)


def blueprint_leaves(blueprint: ExamBlueprint, texts: Mapping[str, str]) -> tuple[Leaf, ...]:
    """Every answerable leaf of the paper in paper order; ``texts`` maps leaf labels to the
    question wording (missing: empty, then only labels can find it)."""
    found: list[Leaf] = []
    for section in blueprint.sections:
        for slot in section.slots():
            for part in slot.parts or (None,):
                label = leaf_label(slot, part)
                found.append(
                    Leaf(
                        label=label,
                        slot=slot.label,
                        part=None if part is None else part.label.lower(),
                        section=section.label,
                        text=texts.get(label, ""),
                    )
                )
    return tuple(found)


def _slot_number(label: str) -> int | None:
    digits = "".join(c for c in label if c.isdigit())
    return int(digits) if digits else None


@dataclass
class _Block:
    lines: list[Line]
    label: Label | None
    """The label its first line starts with (at the margin), if any."""
    evidence: float
    """How strongly its first line looks like the start of an answer."""
    section: str | None
    """The section named by the last heading before it."""
    headings: list[Line] = field(default_factory=list)
    """Section heading lines just above it (they join its segment)."""
    sims: dict[str, float] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return " ".join(ln.text for ln in self.lines if ln.text)

    @property
    def mean_similarity(self) -> float:
        return sum(self.sims.values()) / len(self.sims) if self.sims else 0.0


_PRE = "<before>"
_TRAY = "<tray>"


@dataclass
class _Piece:
    """A run of blocks given the same choice."""

    choice: str
    blocks: list[_Block]
    figures: list[Region] = field(default_factory=list)

    @property
    def lines(self) -> list[Line]:
        return [ln for b in self.blocks for ln in (*b.headings, *b.lines)]


class Segmenter:
    def __init__(
        self,
        blueprint: ExamBlueprint,
        texts: Mapping[str, str],
        embedder: Embedder,
        policy: SegmentationPolicy | None = None,
    ) -> None:
        self._policy = policy or SegmentationPolicy()
        self._leaves = blueprint_leaves(blueprint, texts)
        self._embedder = embedder
        self._slots: dict[int, QuestionSlot] = {}
        for section in blueprint.sections:
            for slot in section.slots():
                number = _slot_number(slot.label)
                if number is not None:
                    self._slots.setdefault(number, slot)
        self._sections = [s.label for s in blueprint.sections]
        self._by_label = {leaf.label: leaf for leaf in self._leaves}
        self._cache: dict[str, Vector] = {}
        worded = [leaf for leaf in self._leaves if leaf.text.strip()]
        vectors = self._embed([leaf.text for leaf in worded])
        self._question_vectors = {leaf.label: v for leaf, v in zip(worded, vectors, strict=True)}

    # --- similarity ------------------------------------------------------------------------

    def _embed(self, texts: Sequence[str]) -> list[Vector]:
        missing = [t for t in dict.fromkeys(texts) if t not in self._cache]
        if missing:
            for text, vector in zip(missing, self._embedder.embed(missing), strict=True):
                self._cache[text] = tuple(vector)
        return [self._cache[t] for t in texts]

    def similarities(self, text: str) -> dict[str, float]:
        """Similarity of ``text`` to every worded question, by leaf label."""
        if not text.strip() or not self._question_vectors:
            return {}
        vector = self._embed([text])[0]
        return {label: cosine(vector, q) for label, q in self._question_vectors.items()}

    # --- the whole booklet -------------------------------------------------------------------

    def segment(self, pages: Sequence[PageInput]) -> SegmentationResult:
        laid = [page_lines(p) for p in sorted(pages, key=lambda p: p.index)]
        order = page_order(laid, self._policy.page_number_share)
        by_id = {p.page.page_id: p for p in laid}
        ordered = [by_id[pid] for pid in order.order]
        blocks = self._blocks(ordered)
        if len(self._leaves) == 1:
            pieces = [_Piece(self._leaves[0].label, blocks)] if blocks else []
        else:
            pieces = self._decode(blocks)
        if not pieces and any(p.figures for p in ordered):
            pieces = [_Piece(_TRAY, [])]
        return SegmentationResult(
            order=order,
            segments=self._finish(pieces, ordered),
            slivers=sum(len(p.slivers) for p in ordered),
            embedder=self._embedder.ref,
        )

    # --- blocks ------------------------------------------------------------------------------

    def _blocks(self, pages: Sequence[PageLines]) -> list[_Block]:
        p = self._policy
        blocks: list[_Block] = []
        section: str | None = None
        headings: list[Line] = []
        for page in pages:
            previous: Line | None = None
            number = page_number_line(page)
            for line in page.lines:
                if line.region_id == number:
                    continue  # the written page number belongs to no answer
                heading = self._heading(line)
                if heading is not None:
                    section = heading
                    headings.append(line)
                    previous = line
                    continue
                evidence, label = self._start_evidence(page, line, previous)
                if evidence > 0 or not blocks or headings:
                    blocks.append(_Block([line], label, evidence, section, headings))
                    headings = []
                else:
                    blocks[-1].lines.append(line)
                previous = line
        if headings:  # a heading after the last answer: keep its lines with the last block
            if blocks:
                blocks[-1].lines.extend(headings)
            else:
                blocks.append(_Block(headings, None, 0.0, section))
        blocks = self._merge_lists(blocks)
        texts = [b.text[: p.block_chars] for b in blocks]
        for block, text in zip(blocks, texts, strict=True):
            block.sims = self.similarities(text)
        return blocks

    def _merge_lists(self, blocks: list[_Block]) -> list[_Block]:
        """A run of at least ``list_run`` blocks numbered n, n+1, n+2… with bare labels, each
        a line or two, restarting at 1 after an answer has begun, is a list inside that answer
        (``9. … 1. question hour  2. zero hour``), not a run of answers: its items join the
        block before the run. Short answers numbered from the start of the booklet (an
        objective section) are not a list."""
        p = self._policy
        merged: list[_Block] = []
        run: list[_Block] = []

        def answering() -> bool:
            return any(b.label is not None and b.label.kind is LabelKind.QUESTION for b in merged)

        def close() -> None:
            first = run[0].label if run else None
            restarts = first is not None and first.number == 1
            if len(run) >= p.list_run and restarts and answering():
                for block in run:
                    merged[-1].lines.extend((*block.headings, *block.lines))
            else:
                merged.extend(run)
            run.clear()

        for block in blocks:
            label = block.label
            bare = (
                label is not None
                and label.kind is LabelKind.QUESTION
                and not label.strong
                and label.number is not None
                and len(block.lines) <= p.list_item_lines
            )
            if not bare or label is None:
                close()
                merged.append(block)
                continue
            last = run[-1].label.number if run and run[-1].label is not None else None
            if run and (last is None or label.number != last + 1):
                close()
            run.append(block)
        close()
        return merged

    def _start_evidence(
        self, page: PageLines, line: Line, previous: Line | None
    ) -> tuple[float, Label | None]:
        p = self._policy
        if line.in_figure or not line.first_in_row:
            return (p.start_page_top if previous is None else 0.0), None
        evidence = 0.0
        label = None
        width = page.page.width
        if line.box.x0 <= page.text_left + p.label_margin * width:
            label = parse_label(line.text)
            if label is not None and label.kind is LabelKind.SECTION:
                label = None
        hang = (page.text_left - line.box.x0) / width
        outdented = hang >= p.outdent
        if label is not None:
            # a lone sub-part marker ("(ii)") is a start only inside a question with parts,
            # which the decoder knows; here it is as weak as an unread mark
            evidence += p.start_mark if label.kind is LabelKind.PART else p.start_label
        elif outdented and looks_like_mark(line.text):
            evidence += p.start_mark
        if hang >= p.min_outdent:
            evidence += p.start_outdent * min(1.0, hang / p.outdent)
        if previous is None:
            evidence += p.start_page_top
        elif page.line_pitch > 0 and line.box.y0 - previous.box.y1 > p.gap_factor * page.line_pitch:
            evidence += p.start_gap
        return evidence, label

    def _heading(self, line: Line) -> str | None:
        """The section a heading line names (anywhere on the line: headings are centred)."""
        if line.in_figure:
            return None
        label = parse_label(line.text)
        if label is None or label.kind is not LabelKind.SECTION or label.section is None:
            return None
        for name in self._sections:
            if name.upper() == label.section:
                return name
        if label.section.isdigit() and 1 <= int(label.section) <= len(self._sections):
            return self._sections[int(label.section) - 1]
        return None

    # --- decoding ----------------------------------------------------------------------------

    def _label_leaves(self, label: Label | None, previous: str | None) -> set[str]:
        """The leaves a block's label names (a sub-part needs the previous block's slot)."""
        if label is None:
            return set()
        if label.kind is LabelKind.PART:
            leaf = self._by_label.get(previous or "")
            if leaf is None or leaf.part is None:
                return set()
            named = self._by_label.get(f"{leaf.slot}.{label.part}")
            return {named.label} if named is not None else set()
        if label.number is None or label.number not in self._slots:
            return set()
        slot = self._slots[label.number]
        if not slot.parts:
            return {slot.label}
        parts = [p.label.lower() for p in slot.parts]
        if label.part in parts:
            return {f"{slot.label}.{label.part}"}
        return {f"{slot.label}.{parts[0]}"}

    def _emission(self, block: _Block, state: str, previous: str | None) -> float:
        p = self._policy
        if state == _TRAY:
            return p.unassigned_score
        if state == _PRE:
            return p.preamble_score if block.label is None else -math.inf
        leaf = self._by_label[state]
        score = 0.0
        if block.sims:
            mean = block.mean_similarity
            score += p.similarity_weight * (block.sims.get(state, mean) - mean)
        if block.label is not None and state in self._label_leaves(block.label, previous):
            score += p.strong_label_weight if block.label.strong else p.label_weight
        if block.section is not None and leaf.section != block.section:
            score -= p.section_penalty
        return score

    def _transition(self, block: _Block, previous: str, state: str) -> float:
        if previous == state:
            return 0.0
        if state == _PRE:
            return -math.inf  # nothing comes back to "before the first answer"
        return block.evidence - self._policy.switch_cost

    def _decode(self, blocks: Sequence[_Block]) -> list[_Piece]:
        """Beam search over the blocks; a hypothesis remembers the questions it answered, so
        starting one again costs ``repeat_penalty`` (a list's "1." inside a later answer)."""
        if not blocks:
            return []
        p = self._policy
        states = [_PRE, _TRAY, *(leaf.label for leaf in self._leaves)]
        for block in blocks:
            named = self._label_leaves(block.label, None)
            if (
                named
                and block.section is not None
                and block.label is not None
                and block.label.kind is LabelKind.QUESTION
                and all(self._by_label[n].section != block.section for n in named)
            ):
                # a number the paper has, but not in the section being answered: a point of
                # a list, or a misread label, so only a weak sign of a start
                block.evidence -= p.start_label - p.start_mark
                block.label = None
        # Emissions depend on the previous state only for a sub-part label ("(b)" after 12.a).
        fixed = [
            None
            if b.label is not None and b.label.kind is LabelKind.PART
            else {s: self._emission(b, s, None) for s in states}
            for b in blocks
        ]
        # hypothesis: (score, path of states, questions answered)
        beam: list[tuple[float, tuple[str, ...], frozenset[str]]] = []
        for state in states:
            value = self._emission(blocks[0], state, None)
            if value > -math.inf:
                answered = frozenset({state}) if state in self._by_label else frozenset()
                beam.append((value, (state,), answered))
        for k, block in enumerate(blocks[1:], start=1):
            emitted = fixed[k]
            grown: dict[tuple[str, frozenset[str]], tuple[float, tuple[str, ...]]] = {}
            for value, path, answered in beam:
                previous = path[-1]
                for state in states:
                    step = self._transition(block, previous, state)
                    if step == -math.inf:
                        continue
                    if state != previous and state in answered:
                        step -= p.repeat_penalty
                    emission = (
                        emitted[state]
                        if emitted is not None
                        else self._emission(block, state, previous)
                    )
                    total = value + step + emission
                    now = answered | {state} if state in self._by_label else answered
                    key = (state, now)
                    if key not in grown or grown[key][0] < total:
                        grown[key] = (total, (*path, state))
            ranked = sorted(grown.items(), key=lambda item: -item[1][0])[: p.beam]
            beam = [(total, path, key[1]) for key, (total, path) in ranked]
        best = max(beam, key=lambda h: h[0])[1]

        pieces: list[_Piece] = []
        for block, state in zip(blocks, best, strict=True):
            starts_anew = block.label is not None and pieces and pieces[-1].choice == _TRAY
            if pieces and pieces[-1].choice == state and not starts_anew:
                pieces[-1].blocks.append(block)
            else:
                pieces.append(_Piece(state, [block]))
        return pieces

    # --- figures, spans, flags ----------------------------------------------------------------

    def _finish(
        self, pieces: list[_Piece], pages: Sequence[PageLines]
    ) -> tuple[ProposedSegment, ...]:
        owner: dict[RegionId, int] = {}
        for number, piece in enumerate(pieces):
            for line in piece.lines:
                owner[line.region_id] = number
        if pieces:
            # A figure joins the segment of the last line above its middle (reading order);
            # one at the top of a page continues the segment from the page before.
            last = 0
            for page in pages:
                marks = [
                    ((ln.box.y0 + ln.box.y1) / 2, owner[ln.region_id])
                    for ln in page.lines
                    if ln.region_id in owner
                ]
                for figure in page.figures:
                    middle = (figure.box.y0 + figure.box.y1) / 2
                    above = [o for y, o in marks if y <= middle]
                    pieces[above[-1] if above else last].figures.append(figure)
                if marks:
                    last = marks[-1][1]

        counts: dict[str, int] = {}
        for piece in pieces:
            if piece.choice in self._by_label:
                counts[piece.choice] = counts.get(piece.choice, 0) + 1
        result: list[ProposedSegment] = []
        for piece in pieces:
            ids = {ln.region_id for ln in piece.lines} | {f.id for f in piece.figures}
            if not ids:
                continue
            region_ids, spans = spans_for(ids, pages)
            result.append(self._proposal(piece, counts, region_ids, spans, len(result)))
        return tuple(result)

    def _proposal(
        self,
        piece: _Piece,
        counts: Mapping[str, int],
        region_ids: tuple[RegionId, ...],
        spans: tuple[SegmentSpan, ...],
        position: int,
    ) -> ProposedSegment:
        first = piece.blocks[0] if piece.blocks else None
        sims = self.similarities(" ".join(b.text for b in piece.blocks)[: self._policy.block_chars])
        best = max(sims, key=lambda k: sims[k]) if sims else None
        flags: list[SegmentFlag] = []
        if piece.choice == _PRE:
            return ProposedSegment(
                slot_label=None,
                source=SegmentSource.RULE,
                region_ids=region_ids,
                spans=spans,
                position=position,
                match_score=None if best is None else round(sims[best], 4),
                flags=(SegmentFlag.BEFORE_FIRST_ANSWER,),
                proposed_label=best,
            )
        label = None if first is None else first.label
        named = self._label_leaves(label, None)
        if label is not None and (label.kind is LabelKind.ANSWER or (not named and label.strong)):
            flags.append(SegmentFlag.NUMBER_UNREAD)
        if piece.choice == _TRAY:
            return ProposedSegment(
                slot_label=None,
                source=SegmentSource.SIMILARITY,
                region_ids=region_ids,
                spans=spans,
                position=position,
                match_score=None if best is None else round(sims[best], 4),
                flags=tuple(flags),
                proposed_label=best,
            )
        by_label = piece.choice in named or (
            label is not None and label.kind is LabelKind.PART and first is not None
        )
        score = sims.get(piece.choice)
        if not by_label and (score is None or score < self._policy.similarity_threshold):
            return ProposedSegment(
                slot_label=None,
                source=SegmentSource.SIMILARITY,
                region_ids=region_ids,
                spans=spans,
                position=position,
                match_score=None if score is None else round(score, 4),
                flags=tuple(flags),
                proposed_label=piece.choice,
            )
        if (
            named
            and piece.choice not in named
            and label is not None
            and label.kind is not (LabelKind.PART)
        ):
            flags.append(SegmentFlag.LABEL_DISAGREES)
        if counts.get(piece.choice, 0) > 1:
            flags.append(SegmentFlag.DUPLICATE)
        return ProposedSegment(
            slot_label=piece.choice,
            source=SegmentSource.RULE if by_label else SegmentSource.SIMILARITY,
            region_ids=region_ids,
            spans=spans,
            position=position,
            match_score=None if by_label or score is None else round(score, 4),
            flags=tuple(dict.fromkeys(flags)),
        )


def reading_items(page: PageLines) -> list[tuple[RegionId, Box]]:
    """The page's lines in reading order, each figure placed after the last line above its
    middle."""
    keyed: list[tuple[tuple[int, int], RegionId, Box]] = [
        ((k, 0), ln.region_id, ln.box) for k, ln in enumerate(page.lines)
    ]
    middles = [(ln.box.y0 + ln.box.y1) / 2 for ln in page.lines]
    for figure in page.figures:
        middle = (figure.box.y0 + figure.box.y1) / 2
        above = sum(1 for y in middles if y <= middle)
        keyed.append(((above - 1, 1), figure.id, figure.box))
    keyed.sort(key=lambda item: item[0])
    return [(rid, box) for _, rid, box in keyed]


def spans_for(
    region_ids: set[RegionId] | frozenset[RegionId], pages: Sequence[PageLines]
) -> tuple[tuple[RegionId, ...], tuple[SegmentSpan, ...]]:
    """The regions in reading order and one span per run of consecutive regions on a page."""
    ordered: list[RegionId] = []
    spans: list[SegmentSpan] = []
    for page in pages:
        run: list[Box] = []
        for rid, box in reading_items(page):
            if rid in region_ids:
                ordered.append(rid)
                run.append(box)
            elif run:
                spans.append(_span(page.page.page_id, run))
                run = []
        if run:
            spans.append(_span(page.page.page_id, run))
    return tuple(ordered), tuple(spans)


def _span(page_id: PageId, boxes: Sequence[Box]) -> SegmentSpan:
    return SegmentSpan(
        page_id=page_id,
        box=Box(
            x0=min(b.x0 for b in boxes),
            y0=min(b.y0 for b in boxes),
            x1=max(b.x1 for b in boxes),
            y1=max(b.y1 for b in boxes),
        ),
    )
