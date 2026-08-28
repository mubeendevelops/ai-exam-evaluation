#!/usr/bin/env python3
"""
scripts/load_reference_diagram.py — Task 4: loads a reference (digital)
diagram's graph structure into content_assets.structured_data.

Reference diagrams are hand-authored JSON for v1 (plan.md §4.4) — small
graphs, no extraction dependency, sidesteps the not-yet-decided extraction
engine entirely on the reference side. The JSON file must be shaped per
plan.md §3:

    {"nodes": [{"node_id": "n1", "label": "CPU"}, ...],
     "edges": [{"edge_id": "e1", "from_node": "n1", "to_node": "n2",
                "label": null}, ...]}

Optionally links the resulting content_assets row via question_asset_links
— pass exactly one of --question-id (role='question_source') or
--variant-id (role='answer_component'), or neither to load the asset
unlinked for now.

Usage:
    export PGHOST=... PGDATABASE=... PGUSER=... PGPASSWORD=...

    python3 scripts/load_reference_diagram.py diagram.json --question-id <uuid>
    python3 scripts/load_reference_diagram.py diagram.json --variant-id <uuid>
    python3 scripts/load_reference_diagram.py diagram.json --dry-run
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db as db_mod   # noqa: E402

SCHEMA_VERSION = 1


def _validate_graph(graph: dict) -> None:
    if "nodes" not in graph or not isinstance(graph["nodes"], list) or not graph["nodes"]:
        raise ValueError("Reference diagram must have a non-empty 'nodes' list.")

    seen_node_ids = set()
    for node in graph["nodes"]:
        if "node_id" not in node or "label" not in node:
            raise ValueError(f"Node missing 'node_id' or 'label': {node!r}")
        if node["node_id"] in seen_node_ids:
            raise ValueError(f"Duplicate node_id: {node['node_id']}")
        seen_node_ids.add(node["node_id"])

    seen_edge_ids = set()
    for edge in graph.get("edges", []):
        for key in ("edge_id", "from_node", "to_node"):
            if key not in edge:
                raise ValueError(f"Edge missing '{key}': {edge!r}")
        if edge["edge_id"] in seen_edge_ids:
            raise ValueError(f"Duplicate edge_id: {edge['edge_id']}")
        seen_edge_ids.add(edge["edge_id"])
        if edge["from_node"] not in seen_node_ids or edge["to_node"] not in seen_node_ids:
            raise ValueError(
                f"Edge {edge['edge_id']} references an unknown node_id "
                f"(from={edge['from_node']}, to={edge['to_node']})"
            )


def load_reference_diagram(cur, graph: dict, question_id: str | None = None,
                            variant_id: str | None = None) -> str:
    """Inserts one content_assets row (asset_type='diagram') holding the
    graph, optionally linked via question_asset_links. Caller owns the
    transaction. Returns the new asset_id."""
    _validate_graph(graph)

    structured_data = {
        "schema_version": SCHEMA_VERSION,
        "nodes": graph["nodes"],
        "edges": graph.get("edges", []),
    }

    asset_id = str(uuid.uuid4())
    cur.execute("""
        INSERT INTO content_assets (asset_id, asset_type, blob_url, structured_data, uploaded_at)
        VALUES (%s, 'diagram', NULL, %s, now())
    """, (asset_id, json.dumps(structured_data)))

    if question_id:
        cur.execute("SELECT question_id FROM questions WHERE question_id = %s", (question_id,))
        if cur.fetchone() is None:
            raise ValueError(f"question_id {question_id} not found")
        cur.execute("""
            INSERT INTO question_asset_links (link_id, asset_id, role, question_id, reference_answer_variant_id)
            VALUES (%s, %s, 'question_source', %s, NULL)
        """, (str(uuid.uuid4()), asset_id, question_id))

    if variant_id:
        cur.execute("SELECT variant_id FROM reference_answer_variants WHERE variant_id = %s", (variant_id,))
        if cur.fetchone() is None:
            raise ValueError(f"variant_id {variant_id} not found")
        cur.execute("""
            INSERT INTO question_asset_links (link_id, asset_id, role, question_id, reference_answer_variant_id)
            VALUES (%s, %s, 'answer_component', NULL, %s)
        """, (str(uuid.uuid4()), asset_id, variant_id))

    return asset_id


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("json_path", type=Path)
    ap.add_argument("--question-id", default=None,
                    help="link as role='question_source' to this question")
    ap.add_argument("--variant-id", default=None,
                    help="link as role='answer_component' to this reference_answer_variant")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the result, roll back instead of committing")
    args = ap.parse_args()

    if args.question_id and args.variant_id:
        ap.error("pass at most one of --question-id / --variant-id")

    graph = json.loads(args.json_path.read_text())

    conn = db_mod.get_connection()
    try:
        with conn.cursor() as cur:
            asset_id = load_reference_diagram(
                cur, graph, question_id=args.question_id, variant_id=args.variant_id
            )
            print(f"Loaded reference diagram -> asset_id={asset_id} "
                  f"({len(graph['nodes'])} nodes, {len(graph.get('edges', []))} edges)")

            if args.dry_run:
                conn.rollback()
                print("[dry-run] rolled back, no changes persisted.")
            else:
                conn.commit()
                print("Committed.")
    except ValueError as e:
        conn.rollback()
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
