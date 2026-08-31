"""
scripts/benchmark_diagram_extraction.py — scores core/diagram_extractor.py +
core/diagram_shapes.py against the labeled diagram benchmark set
(scripts/generate_diagram_benchmark.py) and reports node/edge
precision/recall per diagram family.

This is the empirical half of the shape/edge pipeline, and it stands in the
same relation to core/diagram_shapes.py as scripts/benchmark_ocr_engines.py
does to core/ocr_fallback.py: that module's docstring makes an explicit,
falsifiable claim about SCOPE — validated on rectangular boxes with straight
connectors, unvalidated on diamonds, circles/ellipses and curved connectors —
and this script is what turns that claim into numbers per family instead of
prose. Re-run it after ANY change to shape classification, shape pairing or
edge detection, and before widening what the module docstring claims to
support.

WHAT IS MEASURED, and why each is separate:

  node precision/recall  Did the pipeline find a node WHERE one is drawn?
                         Matched geometrically — an extracted node counts
                         against a ground-truth node when its text bbox's
                         centre falls inside that shape's drawn bbox — so a
                         node that was found but misread still counts as
                         found. Conflating "found it" with "read it" is what
                         makes a shape-detection regression look like an OCR
                         regression.
  label accuracy         Of the nodes that WERE found, how many did OCR read
                         closely enough (difflib ratio >= LABEL_MATCH_THRESHOLD)
                         to survive glossary matching downstream. This is an
                         OCR number, reported here only so a node-detection
                         change can be seen not to have moved it.
  shape accuracy         Of the nodes that were found, how many got the right
                         shape_type. This is the column diamonds and ellipses
                         exist to move.
  edge precision/recall  Detected connectors, compared UNDIRECTED. Extracted
                         node ids are mapped to ground-truth labels through
                         the node matching above, so an edge can only match
                         if both its endpoints resolved to the right nodes; a
                         detected edge with an unmapped endpoint counts as a
                         false positive rather than being quietly ignored.

DIRECTION IS DELIBERATELY NOT SCORED. core/diagram_shapes.py's
ARROW_DENSITY_MARGIN docstring already states that arrowhead detection is
unreliable exactly where a connector meets a shape's own ink, and reports
"undirected" rather than guessing. Scoring direction here would mostly
measure that known, documented gap over and over; core/diagram_evaluator.py
already treats a reversed edge as a match for the same reason.

Usage:
    python scripts/generate_diagram_benchmark.py     # once, writes the fixtures
    python scripts/benchmark_diagram_extraction.py
    python scripts/benchmark_diagram_extraction.py --save before.json
    python scripts/benchmark_diagram_extraction.py --baseline before.json
"""
from __future__ import annotations

import argparse
import difflib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PIL import Image

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
BENCHMARK_DIR = REPO_ROOT / "media" / "diagram_benchmark"
IMAGES_DIR = BENCHMARK_DIR / "images"
GROUND_TRUTH_PATH = BENCHMARK_DIR / "ground_truth.json"

#: difflib ratio at or above which a read label counts as "read correctly".
#: Same order as core/diagram_evaluator.GLOSSARY_FUZZY_THRESHOLD (0.75) but
#: deliberately looser: downstream, a near-miss still gets rescued by
#: glossary aliases and embedding similarity, so scoring this at the
#: glossary's own threshold would under-report what the pipeline as a whole
#: recovers.
LABEL_MATCH_THRESHOLD = 0.6

#: Reported columns, in table order.
METRICS = ("node_p", "node_r", "node_f1", "edge_p", "edge_r", "edge_f1",
           "label_acc", "shape_acc")


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _centre(bbox: list[int]) -> tuple[float, float]:
    x, y, w, h = bbox
    return (x + w / 2, y + h / 2)


def _inside(point: tuple[float, float], bbox: list[int]) -> bool:
    x, y, w, h = bbox
    return x <= point[0] <= x + w and y <= point[1] <= y + h


def match_nodes(truth_nodes: list[dict], extracted_nodes: list[dict]) -> dict:
    """Greedy geometric matching: an extracted node is a candidate for a
    ground-truth node when its own text bbox's centre lies inside that
    node's drawn shape bbox. Ties (two reads inside one shape — a split
    label, or a stray mark) are broken by label similarity, so the read that
    actually IS the label wins the pairing and the other is correctly
    counted as a false positive.

    Returns {"pairs": [(truth_index, extracted_index, label_ratio)],
             "matched_ext": set, "matched_truth": set}."""
    candidates = []
    for ti, truth in enumerate(truth_nodes):
        for ei, ext in enumerate(extracted_nodes):
            if ext.get("bbox") is None or not _inside(_centre(ext["bbox"]), truth["bbox"]):
                continue
            ratio = difflib.SequenceMatcher(
                None, (ext.get("label") or "").strip().lower(),
                truth["label"].strip().lower()).ratio()
            candidates.append((ratio, ti, ei))
    candidates.sort(reverse=True, key=lambda c: c[0])

    matched_truth: set[int] = set()
    matched_ext: set[int] = set()
    pairs = []
    for ratio, ti, ei in candidates:
        if ti in matched_truth or ei in matched_ext:
            continue
        matched_truth.add(ti)
        matched_ext.add(ei)
        pairs.append((ti, ei, ratio))
    return {"pairs": pairs, "matched_ext": matched_ext, "matched_truth": matched_truth}


def score_image(truth: dict, graph: dict) -> dict:
    """All eight metrics for one image, plus the raw counts behind them (so
    a surprising rate can be traced to how few items it was computed over —
    four edges per image means one miss is 25%)."""
    truth_nodes = truth["nodes"]
    ext_nodes = graph.get("nodes", [])
    ext_edges = graph.get("edges", [])

    match = match_nodes(truth_nodes, ext_nodes)
    pairs = match["pairs"]

    true_positives = len(pairs)
    node_p = true_positives / len(ext_nodes) if ext_nodes else (1.0 if not truth_nodes else 0.0)
    node_r = true_positives / len(truth_nodes) if truth_nodes else 1.0

    label_hits = sum(1 for _ti, _ei, ratio in pairs if ratio >= LABEL_MATCH_THRESHOLD)
    shape_hits = sum(1 for ti, ei, _ratio in pairs
                     if ext_nodes[ei].get("shape_type") == truth_nodes[ti]["shape_type"])

    # Extracted node_id -> ground-truth label, for whichever nodes matched.
    id_to_label = {ext_nodes[ei]["node_id"]: truth_nodes[ti]["label"]
                   for ti, ei, _ratio in pairs}

    truth_pairs = {frozenset((e["from"], e["to"])) for e in truth["edges"]}
    detected_pairs = set()
    unmappable_edges = 0
    for edge in ext_edges:
        a = id_to_label.get(edge["from_node"])
        b = id_to_label.get(edge["to_node"])
        if a is None or b is None or a == b:
            unmappable_edges += 1
            continue
        detected_pairs.add(frozenset((a, b)))

    edge_tp = len(detected_pairs & truth_pairs)
    # Denominator counts every detected edge, mapped or not: a connector
    # invented between two things that aren't nodes is a false positive, not
    # a non-event.
    detected_total = len(detected_pairs) + unmappable_edges
    edge_p = edge_tp / detected_total if detected_total else (1.0 if not truth_pairs else 0.0)
    edge_r = edge_tp / len(truth_pairs) if truth_pairs else 1.0

    return {
        "node_p": node_p, "node_r": node_r, "node_f1": _f1(node_p, node_r),
        "edge_p": edge_p, "edge_r": edge_r, "edge_f1": _f1(edge_p, edge_r),
        "label_acc": label_hits / true_positives if true_positives else 0.0,
        "shape_acc": shape_hits / true_positives if true_positives else 0.0,
        "counts": {
            "truth_nodes": len(truth_nodes), "extracted_nodes": len(ext_nodes),
            "matched_nodes": true_positives,
            "truth_edges": len(truth_pairs), "detected_edges": detected_total,
            "matched_edges": edge_tp, "unmappable_edges": unmappable_edges,
        },
        "shape_types_seen": sorted({n["shape_type"] for n in ext_nodes if n.get("shape_type")}),
    }


def _load_ground_truth() -> dict:
    if not GROUND_TRUTH_PATH.exists():
        raise SystemExit(
            f"{GROUND_TRUTH_PATH} not found — run "
            f"scripts/generate_diagram_benchmark.py first."
        )
    return json.loads(GROUND_TRUTH_PATH.read_text())


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _print_report(per_image: dict, baseline: dict | None) -> None:
    """Per-family means (the unit the scope claim is actually made in), then
    the overall mean. When --baseline is given, each cell carries its delta
    against that run — the before/after this benchmark exists to produce."""
    by_family: dict[str, list[dict]] = {}
    for name, result in per_image.items():
        by_family.setdefault(result["family"], []).append(result["metrics"])

    base_by_family: dict[str, list[dict]] = {}
    if baseline:
        for name, result in baseline.get("per_image", {}).items():
            base_by_family.setdefault(result["family"], []).append(result["metrics"])

    width = 11 if not baseline else 17
    header = f"{'family':<16}" + "".join(f"{m:>{width}}" for m in METRICS)
    print("\n" + header)
    print("-" * len(header))

    for family in sorted(by_family):
        cells = []
        for metric in METRICS:
            value = _mean([m[metric] for m in by_family[family]])
            if baseline and family in base_by_family:
                before = _mean([m[metric] for m in base_by_family[family]])
                cells.append(f"{value:>6.3f} ({value - before:+.3f})".rjust(width))
            else:
                cells.append(f"{value:>{width}.3f}")
        print(f"{family:<16}" + "".join(cells))

    print("-" * len(header))
    overall_cells = []
    all_metrics = [m for results in by_family.values() for m in results]
    base_all = [m for results in base_by_family.values() for m in results]
    for metric in METRICS:
        value = _mean([m[metric] for m in all_metrics])
        if baseline and base_all:
            before = _mean([m[metric] for m in base_all])
            overall_cells.append(f"{value:>6.3f} ({value - before:+.3f})".rjust(width))
        else:
            overall_cells.append(f"{value:>{width}.3f}")
    print(f"{'ALL':<16}" + "".join(overall_cells))

    print("\nCounts per family (summed over styles): "
          "matched/truth nodes, matched/detected/truth edges")
    for family in sorted(by_family):
        counts = [m["counts"] for m in by_family[family]]
        summed = {k: sum(c[k] for c in counts) for k in counts[0]}
        shapes = sorted({s for name, r in per_image.items() if r["family"] == family
                         for s in r["metrics"]["shape_types_seen"]})
        print(f"  {family:<16} nodes {summed['matched_nodes']}/{summed['truth_nodes']} "
              f"(extracted {summed['extracted_nodes']})   "
              f"edges {summed['matched_edges']}/{summed['detected_edges']} detected, "
              f"{summed['truth_edges']} drawn   shape_types={shapes or ['-']}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--save", default=None,
                    help="write the full per-image results to this JSON file, "
                         "for use as a later run's --baseline")
    ap.add_argument("--baseline", default=None,
                    help="a JSON file written by an earlier --save; every cell "
                         "is reported with its delta against it")
    ap.add_argument("--family", default=None,
                    help="only run the images in this family (see "
                         "generate_diagram_benchmark.py's FAMILIES)")
    args = ap.parse_args()

    from core.diagram_extractor import extract_diagram_structure

    ground_truth = _load_ground_truth()
    baseline = json.loads(pathlib.Path(args.baseline).read_text()) if args.baseline else None

    per_image = {}
    for filename, truth in sorted(ground_truth.items()):
        if args.family and truth["family"] != args.family:
            continue
        image = Image.open(IMAGES_DIR / filename).convert("RGB")
        graph = extract_diagram_structure(image)
        metrics = score_image(truth, graph)
        per_image[filename] = {"family": truth["family"], "style": truth["style"],
                               "metrics": metrics}
        counts = metrics["counts"]
        print(f"{filename:<40} nodes {counts['matched_nodes']}/{counts['truth_nodes']}  "
              f"edges {counts['matched_edges']}/{counts['truth_edges']}  "
              f"(detected {counts['extracted_nodes']} nodes, "
              f"{counts['detected_edges']} edges)")

    if not per_image:
        raise SystemExit(f"No images matched --family {args.family!r}.")

    _print_report(per_image, baseline)

    if args.save:
        path = pathlib.Path(args.save)
        path.write_text(json.dumps({"per_image": per_image}, indent=2) + "\n")
        print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
