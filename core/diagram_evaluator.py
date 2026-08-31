"""
core/diagram_evaluator.py — Task 4: compares an extracted (handwritten/
scanned) diagram graph against a reference (digital) diagram graph.

Both graphs must already be in the shape defined in plan.md §3 /
core/diagram_extractor.py:
    {"schema_version": 1,
     "nodes": [{"node_id", "label", ...}],
     "edges": [{"edge_id", "from_node", "to_node", "label": str | None}]}

Matching is NLP-only for v1 — plain string glossary fuzzy-matching (difflib)
to correct OCR noise, then sentence-transformer label-embedding similarity
for node matching. No LLM involved, mirroring how Task 3
(core/evaluator.py) shipped embeddings-based scoring before adding an LLM
path. An LLM-based anomaly-explanation pass is a deliberately separate,
later addition (plan.md §5 step 5 / §7 phase 2) — not implemented here.
"""
from __future__ import annotations

from core import text_match

# Lazy-loaded on first call — same pattern as core/evaluator.py's
# _get_embedding_model. Kept as its own module-level cache rather than
# importing core.evaluator's private one: diagram evaluation runs as its
# own script/process (scripts/evaluate_diagram_answer.py) and essentially
# never shares a process with text-answer evaluation, so there's no real
# double-loading in practice, and this avoids reaching into another
# module's private state.
_embedding_model = None
_EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

# Cosine similarity threshold for accepting a reference<->extracted node
# label match. Chosen conservatively (short technical labels have less
# context than full-sentence answers, where core/evaluator.py's scoring
# doesn't need a hard accept/reject threshold at all).
NODE_MATCH_THRESHOLD = 0.6

# difflib.SequenceMatcher ratio threshold for accepting a fuzzy glossary
# match (catches OCR noise like "Memroy" -> "Memory"). Below this, the label
# is left uncanonicalized and falls through to embedding-based node
# matching on its raw text.
GLOSSARY_FUZZY_THRESHOLD = 0.75

# A ratio-based threshold unfairly penalizes short acronyms: a single
# character OCR misread on a 3-letter word (e.g. "CPU" -> "CPV", a real
# read at 0.98 engine confidence — this is not noise, see
# core/diagram_extractor.py) already drops the SequenceMatcher ratio to
# 0.667, below GLOSSARY_FUZZY_THRESHOLD. For short strings, an absolute
# edit-distance check catches this without loosening the ratio threshold
# globally (which would start accepting genuinely-different longer words).
SHORT_LABEL_MAX_LEN = 6
SHORT_LABEL_MAX_EDITS = 1

# Backward-compatible alias: this used to be a private function defined in
# this module. The implementation now lives in core/text_match.py (shared
# with core/plugins/text_extraction.py's keyword/rubric coverage matching),
# but existing callers/tests referencing core.diagram_evaluator._edit_distance
# keep working unchanged.
_edit_distance = text_match.edit_distance


def _get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer  # type: ignore
        _embedding_model = SentenceTransformer(_EMBEDDING_MODEL_NAME)
    return _embedding_model


def match_glossary(label: str, glossary_terms: list[dict]) -> dict | None:
    """Fuzzy-matches one extracted label against glossary_terms (each dict:
    {"term_id", "canonical_term", "aliases": [...]}). Plain string matching
    (difflib), not embeddings — this step exists to correct spelling/OCR
    noise, not to find semantic equivalents.

    Returns {"term_id", "canonical_term", "match_type"} or None if nothing
    clears GLOSSARY_FUZZY_THRESHOLD.

    Delegates the actual string comparison to core/text_match.py::fuzzy_match,
    flattening every term's (canonical_term + aliases) into one ordered
    candidate list first — fuzzy_match's own short-circuit-on-first-hit,
    else-best-of-the-rest semantics reproduce this function's original
    nested-loop behavior exactly when the candidates are flattened in the
    same term-by-term, candidate-by-candidate order."""
    flat_candidates: list[tuple[str, dict]] = []
    for term in glossary_terms:
        for candidate in [term["canonical_term"]] + list(term.get("aliases") or []):
            flat_candidates.append((candidate, term))

    result = text_match.fuzzy_match(
        label, [candidate for candidate, _ in flat_candidates],
        threshold=GLOSSARY_FUZZY_THRESHOLD,
        short_max_len=SHORT_LABEL_MAX_LEN,
        short_max_edits=SHORT_LABEL_MAX_EDITS,
    )
    if result is None:
        return None

    _, term = flat_candidates[result["index"]]
    return {
        "term_id": term["term_id"],
        "canonical_term": term["canonical_term"],
        "match_type": result["match_type"],
    }


def _canonicalize_nodes(nodes: list[dict], glossary_terms: list[dict]) -> tuple[list[dict], list[dict]]:
    """Attaches a glossary match (if any) to each node, under a
    'canonical_label' key used for downstream matching so OCR noise doesn't
    tank node-similarity scores. Returns (canonicalized_nodes,
    glossary_matches_log) — the log becomes part of the final metadata (g)."""
    canonicalized = []
    glossary_matches = []
    for node in nodes:
        match = match_glossary(node["label"], glossary_terms) if glossary_terms else None
        node_copy = dict(node)
        if match:
            node_copy["glossary_term_id"] = match["term_id"]
            node_copy["canonical_label"] = match["canonical_term"]
            glossary_matches.append({
                "extracted_label": node["label"],
                "canonical_term": match["canonical_term"],
                "match_type": match["match_type"],
            })
        else:
            node_copy["canonical_label"] = node["label"]
        canonicalized.append(node_copy)
    return canonicalized, glossary_matches


def _match_nodes(reference_nodes: list[dict], extracted_nodes: list[dict]) -> dict:
    """Greedy best-first bipartite matching by label embedding similarity —
    each node matched at most once, highest-similarity pairs claimed first.
    Not a full optimal assignment (e.g. via the Hungarian algorithm): for
    the small node counts a hand-drawn diagram realistically has (single
    digits to low tens), greedy and optimal essentially never disagree, and
    greedy avoids adding a scipy dependency for this alone.

    Returns {"pairs": [(ref_node_id, extracted_node_id, similarity), ...],
             "matched_ref_ids": set, "matched_extracted_ids": set}."""
    if not reference_nodes or not extracted_nodes:
        return {"pairs": [], "matched_ref_ids": set(), "matched_extracted_ids": set()}

    from sentence_transformers import util as st_util  # type: ignore

    model = _get_embedding_model()
    ref_labels = [n.get("canonical_label", n["label"]) for n in reference_nodes]
    ext_labels = [n.get("canonical_label", n["label"]) for n in extracted_nodes]

    ref_emb = model.encode(ref_labels, convert_to_tensor=True)
    ext_emb = model.encode(ext_labels, convert_to_tensor=True)
    sims = st_util.cos_sim(ref_emb, ext_emb)  # shape [len(ref), len(ext)]

    candidates = []
    for i, ref_node in enumerate(reference_nodes):
        for j, ext_node in enumerate(extracted_nodes):
            score = float(sims[i][j])
            if score >= NODE_MATCH_THRESHOLD:
                candidates.append((score, ref_node["node_id"], ext_node["node_id"]))
    candidates.sort(reverse=True, key=lambda c: c[0])

    matched_ref: set = set()
    matched_ext: set = set()
    pairs = []
    for score, ref_id, ext_id in candidates:
        if ref_id in matched_ref or ext_id in matched_ext:
            continue
        matched_ref.add(ref_id)
        matched_ext.add(ext_id)
        pairs.append((ref_id, ext_id, score))

    return {"pairs": pairs, "matched_ref_ids": matched_ref, "matched_extracted_ids": matched_ext}


def _compare_edges(reference_edges: list[dict], extracted_edges: list[dict],
                    ref_to_ext: dict, ext_to_ref: dict) -> tuple[list[dict], list[str], list[str]]:
    """Compares reference edges against extracted edges via the node-id
    mapping produced by _match_nodes. Returns (edge_comparison_rows,
    missing_edge_messages, anomaly_edge_messages)."""
    extracted_pairs = {(e["from_node"], e["to_node"]) for e in extracted_edges}
    extracted_pairs_reversed = {(e["to_node"], e["from_node"]) for e in extracted_edges}

    edge_rows = []
    missing = []
    matched_extracted_pairs: set = set()

    for edge in reference_edges:
        mapped_from = ref_to_ext.get(edge["from_node"])
        mapped_to = ref_to_ext.get(edge["to_node"])

        if mapped_from is None or mapped_to is None:
            edge_rows.append({
                "from": edge["from_node"], "to": edge["to_node"],
                "status": "missing", "reason": "endpoint node not matched",
            })
            missing.append(
                f"Edge {edge['from_node']}→{edge['to_node']} not found "
                f"(an endpoint node is missing on the extracted side)"
            )
            continue

        if (mapped_from, mapped_to) in extracted_pairs:
            edge_rows.append({"from": edge["from_node"], "to": edge["to_node"], "status": "matched"})
            matched_extracted_pairs.add((mapped_from, mapped_to))
        elif (mapped_from, mapped_to) in extracted_pairs_reversed:
            edge_rows.append({
                "from": edge["from_node"], "to": edge["to_node"], "status": "direction_reversed",
            })
            matched_extracted_pairs.add((mapped_to, mapped_from))
        else:
            edge_rows.append({"from": edge["from_node"], "to": edge["to_node"], "status": "missing"})
            missing.append(f"Edge {edge['from_node']}→{edge['to_node']} not found")

    anomalies = []
    for e in extracted_edges:
        pair = (e["from_node"], e["to_node"])
        if pair in matched_extracted_pairs or (pair[1], pair[0]) in matched_extracted_pairs:
            continue
        ref_from = ext_to_ref.get(e["from_node"])
        ref_to = ext_to_ref.get(e["to_node"])
        note = (f" (maps to reference nodes {ref_from}→{ref_to}, but no such "
                f"reference edge exists)" if ref_from and ref_to else "")
        anomalies.append(f"Extra edge {e['from_node']}→{e['to_node']} with no reference counterpart{note}")

    return edge_rows, missing, anomalies


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def compare_diagrams(extracted: dict, reference: dict, glossary_terms: list[dict] | None,
                      marks_max: float) -> dict:
    """Compares one extracted diagram graph against one reference diagram
    graph. Returns the full metadata dict (plan.md §5, covering the task's
    (a)-(g)) — this whole dict is what callers store in
    evaluation_results.metrics."""
    glossary_terms = glossary_terms or []
    ref_nodes_raw = reference.get("nodes", [])
    ref_edges = reference.get("edges", [])
    ext_nodes_raw = extracted.get("nodes", [])
    ext_edges = extracted.get("edges", [])

    ext_nodes, glossary_matches = _canonicalize_nodes(ext_nodes_raw, glossary_terms)
    # Reference labels are hand-authored (load_reference_diagram.py), not OCR
    # output, so their glossary-match log has no OCR noise to report on —
    # only the extracted side's glossary_matches goes into the metadata (b).
    ref_nodes, _ = _canonicalize_nodes(ref_nodes_raw, glossary_terms)

    match_result = _match_nodes(ref_nodes, ext_nodes)
    pairs = match_result["pairs"]
    ref_to_ext = {ref_id: ext_id for ref_id, ext_id, _ in pairs}
    ext_to_ref = {ext_id: ref_id for ref_id, ext_id, _ in pairs}
    sim_by_ref = {ref_id: score for ref_id, _, score in pairs}

    ext_by_id = {n["node_id"]: n for n in ext_nodes}

    # (e) Node/label validation
    node_validation = []
    missing_info = []
    for node in ref_nodes:
        ext_id = ref_to_ext.get(node["node_id"])
        if ext_id:
            node_validation.append({
                "reference_label": node["label"],
                "matched_label": ext_by_id[ext_id]["label"],
                "status": "matched",
                "similarity": round(sim_by_ref[node["node_id"]], 4),
            })
        else:
            node_validation.append({
                "reference_label": node["label"], "matched_label": None, "status": "missing",
            })
            # (c) Missing information
            missing_info.append(f"Node '{node['label']}' not found in student diagram")

    # (d) Anomalies — extra nodes with no reference counterpart
    anomalies = []
    for node in ext_nodes:
        if node["node_id"] not in ext_to_ref:
            anomalies.append(f"Extra node '{node['label']}' with no reference counterpart")

    # (f) Edge comparison
    edge_comparison, missing_edges, edge_anomalies = _compare_edges(
        ref_edges, ext_edges, ref_to_ext, ext_to_ref
    )
    missing_info.extend(missing_edges)
    anomalies.extend(edge_anomalies)

    # (a) Similarity score — weighted node/edge F1, scaled to marks_max
    node_recall = len(ref_to_ext) / len(ref_nodes) if ref_nodes else 1.0
    node_precision = (len(ref_to_ext) / len(ext_nodes)) if ext_nodes else (1.0 if not ref_nodes else 0.0)
    node_f1 = _f1(node_precision, node_recall)

    matched_edges = sum(1 for row in edge_comparison if row["status"] in ("matched", "direction_reversed"))
    edge_recall = matched_edges / len(ref_edges) if ref_edges else 1.0
    edge_precision = (matched_edges / len(ext_edges)) if ext_edges else (1.0 if not ref_edges else 0.0)
    edge_f1 = _f1(edge_precision, edge_recall)

    combined = 0.6 * node_f1 + 0.4 * edge_f1
    similarity_score = round(combined * marks_max, 2)

    return {
        "schema_version": 1,
        "similarity_score": similarity_score,
        "marks_max": marks_max,
        "node_validation": node_validation,               # (e)
        "edge_comparison": edge_comparison,                # (f)
        "missing_information": missing_info,               # (c)
        "anomalies": anomalies,                            # (d)
        "glossary_matches": glossary_matches,               # (b)
        "scores_breakdown": {
            "node_precision": round(node_precision, 4),
            "node_recall": round(node_recall, 4),
            "node_f1": round(node_f1, 4),
            "edge_precision": round(edge_precision, 4),
            "edge_recall": round(edge_recall, 4),
            "edge_f1": round(edge_f1, 4),
        },
        "model": {
            "matching_method": "embeddings",
            "embedding_model": _EMBEDDING_MODEL_NAME,
        },
    }


def stub_compare(extracted: dict, reference: dict, marks_max: float) -> dict:
    """Deterministic fake comparison for --stub, matching the --stub
    convention in core/evaluator.py. Returns 70% of marks_max, empty detail
    lists — used to exercise the DB-write path without loading the
    embedding model or needing real glossary data."""
    score = round(marks_max * 0.7, 2)
    return {
        "schema_version": 1,
        "similarity_score": score,
        "marks_max": marks_max,
        "node_validation": [],
        "edge_comparison": [],
        "missing_information": [],
        "anomalies": [],
        "glossary_matches": [],
        "scores_breakdown": {"stub": True},
        "model": {"matching_method": "stub"},
    }
