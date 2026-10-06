"""The diagram criterion scorer (design.md "Into marks"): credit = the graph similarity, or one
sub-score (nodes, edges, labels) when the teacher split the diagram into separate criteria
with their own weights. Every diagram of the answer is compared; the best is kept. The R6
document goes with the criterion score (``detail``); the reason names reference-side wording
only."""

from collections.abc import Sequence
from decimal import Decimal

from tarn_core.domain.common import EngineRef
from tarn_core.domain.content import (
    CriterionType,
    DiagramComponent,
    DiagramParams,
)
from tarn_core.domain.diagram import DiagramKind, RecognitionState
from tarn_core.domain.scoring import CHECK, CriterionReason, CriterionScore
from tarn_core.errors import InvariantError
from tarn_core.ports.engines import ScoringInput
from tarn_core.services.diagrams.compare import (
    DiagramComparator,
    DiagramComparison,
    EdgeStatus,
    GraphComparator,
)
from tarn_core.services.diagrams.labels import LabelMatch
from tarn_core.services.diagrams.policy import DiagramPolicy
from tarn_core.services.diagrams.result import comparison_document
from tarn_core.services.scoring.service import MANUAL


class DiagramScorer:
    ref = EngineRef(name="diagram", version="1")

    def __init__(
        self,
        comparators: Sequence[DiagramComparator] | None = None,
        policy: DiagramPolicy | None = None,
    ) -> None:
        self.policy = policy or DiagramPolicy()
        found = list(comparators) if comparators is not None else [GraphComparator(self.policy)]
        self._by_kind: dict[DiagramKind, DiagramComparator] = {}
        for c in found:
            for kind in c.kinds:
                self._by_kind.setdefault(kind, c)

    def supports(self, criterion_type: CriterionType) -> bool:
        return criterion_type is CriterionType.DIAGRAM

    def comparator_for(self, kind: DiagramKind) -> DiagramComparator | None:
        return self._by_kind.get(kind)

    def score(self, item: ScoringInput) -> CriterionScore:
        params = item.criterion.params
        if not isinstance(params, DiagramParams):
            raise InvariantError("the diagram scorer got a criterion of another type")
        reference = item.reference_diagrams.get(params.reference_diagram_id)
        if reference is None:
            return self._manual(item, "the reference diagram is missing: mark by hand")
        if reference.graph.empty or reference.recognition in (
            RecognitionState.PENDING,
            RecognitionState.FAILED,
        ):
            return self._manual(
                item, "the reference diagram has no graph yet: check it, or mark by hand"
            )
        comparator = self._by_kind.get(reference.kind)
        if comparator is None:
            return self._manual(
                item, f"{reference.kind.value} diagrams are not compared yet: mark by hand"
            )
        drawings = [d for d in item.diagrams if not d.graph.empty or d.graph.edited_by_teacher]
        if item.diagrams and not drawings:
            return self._manual(
                item, "the drawing could not be recognised: correct its graph, or mark by hand"
            )
        if not drawings:
            return CriterionScore(
                criterion=item.criterion.ref,
                weight=item.criterion.weight,
                credit=Decimal(0),
                scorer=self.ref,
                flags=(CHECK,),
                similarity=0.0,
                reason=CriterionReason(
                    summary="no diagram was found in this answer: 0 suggested, please check",
                    missing=tuple(reference.graph.labels),
                ),
            )
        glossary = item.glossary
        compared = [(comparator.compare(reference.graph, d.graph, glossary), d) for d in drawings]
        best, diagram = max(compared, key=lambda x: x[0].similarity)
        value = _component(best, params.component)
        flags: list[str] = []
        notes: list[str] = []
        if best.min_confidence < self.policy.low_confidence and not diagram.graph.edited_by_teacher:
            flags.append(CHECK)
            notes.append("parts of the drawing were hard to recognise")
        if reference.recognition is RecognitionState.RECOGNISED:
            if CHECK not in flags:
                flags.append(CHECK)
            notes.append("the reference graph was not checked by a teacher")
        return CriterionScore(
            criterion=item.criterion.ref,
            weight=item.criterion.weight,
            credit=Decimal(str(round(value, 4))),
            scorer=self.ref,
            evidence=_summary(best, params.component),
            flags=tuple(flags),
            similarity=best.similarity,
            reason=CriterionReason(
                summary="; ".join([_summary(best, params.component), *notes]),
                matched=tuple(
                    r.label
                    for r in best.reference_nodes
                    if r.label and r.label_match in (LabelMatch.EXACT, LabelMatch.CLOSE)
                ),
                missing=best.missing_labels,
                found=f"{value:.2f}",
                expected="1.00",
            ),
            detail=comparison_document(
                best,
                question_code=item.question_code,
                slot_label=item.slot_label,
                kind=reference.kind,
                reference_diagram_id=str(reference.id),
                reference_version=reference.meta.version,
                student_diagram_id=diagram.diagram_id,
                student_version=diagram.version,
            ),
        )

    def _manual(self, item: ScoringInput, why: str) -> CriterionScore:
        return CriterionScore(
            criterion=item.criterion.ref,
            weight=item.criterion.weight,
            credit=Decimal(0),
            scorer=self.ref,
            evidence=why,
            flags=(MANUAL,),
            reason=CriterionReason(summary=why),
        )


def _component(c: DiagramComparison, component: DiagramComponent) -> float:
    match component:
        case DiagramComponent.WHOLE:
            return c.similarity
        case DiagramComponent.NODES:
            return c.sub_scores.nodes
        case DiagramComponent.EDGES:
            return c.sub_scores.edges
        case DiagramComponent.LABELS:
            return c.sub_scores.labels


def _summary(c: DiagramComparison, component: DiagramComponent) -> str:
    total_ref = len(c.reference_nodes)
    parts = [
        f"similarity {c.similarity:.2f} (nodes {c.sub_scores.nodes:.2f}, "
        f"edges {c.sub_scores.edges:.2f}, labels {c.sub_scores.labels:.2f})",
        f"{c.matched_nodes} of {total_ref} reference nodes found",
    ]
    if c.missing_edges:
        parts.append(f"{len(c.missing_edges)} edge(s) missing")
    if reversed_ := c.counts(EdgeStatus.REVERSED):
        parts.append(f"{reversed_} arrow(s) reversed")
    if extra := c.counts(EdgeStatus.EXTRA):
        parts.append(f"{extra} extra edge(s)")
    extra_nodes = sum(1 for n in c.nodes if n.matched_ref is None)
    if extra_nodes:
        parts.append(f"{extra_nodes} extra node(s)")
    if component is not DiagramComponent.WHOLE:
        parts.insert(0, f"{component.value} credit")
    return ", ".join(parts)
