#!/usr/bin/env python3
"""
scripts/load_reference_table.py — loads a reference (digital) table's grid
into content_assets.structured_data.

Mirrors scripts/load_reference_diagram.py — the two share their write,
core/reference_assets.insert_reference_asset(), and differ only in the
per-type validation — including its rationale:
reference tables are hand-authored JSON for v1 — small grids, no extraction
dependency on the reference side, so a wrong reference is a typo in a file
rather than an OCR failure nobody notices.

NO NEW TABLE — content_assets is already the generalized diagram/table/
formula store (CLAUDE_CONTEXT.md §5 design rule 3, PROJECT_CONTEXT.md §8;
its asset_type enum has had 'table' since migration 002). A
reference_tables table would duplicate asset_id/blob_url/structured_data/
uploaded_at wholesale for no gain, which is the exact duplication that rule
exists to prevent.

The JSON file must be shaped per core/table_extractor.py's documented
schema, minus the extraction-only fields (a reference is authored, not
read off an image, so it carries no bbox/confidence/ocr_engine):

    {"rows": 4, "cols": 3, "has_header": true,
     "cells": [{"row": 0, "col": 0, "text": "Algorithm"}, ...]}

`rows`/`cols` may be omitted and are then derived from the cells.
media/tables/reference_table.json (written by
scripts/generate_table_benchmark.py) is a working example.

Optionally links the resulting content_assets row via question_asset_links
— pass exactly one of --question-id (role='question_source') or
--variant-id (role='answer_component'), or neither to load the asset
unlinked for now. NOTE: scripts/evaluate_table_answer.py's write goes
through migration 012's trigger, which requires the asset to be linked to
the answer's question via role='question_source' — so an asset loaded
unlinked cannot be scored against until it is linked.

--blob-url optionally records where the reference IMAGE lives. It must be a
stable "bucket/key" reference (CLAUDE_CONTEXT.md §10) — a presigned URL is
rejected outright rather than stored, since it expires and would leave a
dead link in the DB. Generate presigned URLs on demand via
core/storage.py::presigned_get_url() instead.

Usage:
    export PGHOST=... PGDATABASE=... PGUSER=... PGPASSWORD=...

    python3 scripts/load_reference_table.py table.json --question-id <uuid>
    python3 scripts/load_reference_table.py table.json --variant-id <uuid>
    python3 scripts/load_reference_table.py table.json --blob-url refs/table1.png
    python3 scripts/load_reference_table.py table.json --dry-run
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db as db_mod             # noqa: E402
from core import reference_assets         # noqa: E402

#: Version of the reference GRID schema. Not shared with
#: load_reference_diagram.py's constant of the same name: they version
#: different schemas and must be bumped independently.
SCHEMA_VERSION = 1


def _validate_table(table: dict) -> dict:
    """Validates the hand-authored grid and returns it normalized (rows and
    cols filled in from the cells when omitted). Every check here is one a
    typo in a hand-written JSON file would otherwise turn into a silently
    wrong score."""
    cells = table.get("cells")
    if not isinstance(cells, list) or not cells:
        raise ValueError("Reference table must have a non-empty 'cells' list.")

    seen: set[tuple[int, int]] = set()
    for cell in cells:
        for key in ("row", "col", "text"):
            if key not in cell:
                raise ValueError(f"Cell missing '{key}': {cell!r}")
        if not isinstance(cell["row"], int) or not isinstance(cell["col"], int):
            raise ValueError(f"Cell 'row'/'col' must be integers: {cell!r}")
        if cell["row"] < 0 or cell["col"] < 0:
            raise ValueError(f"Cell 'row'/'col' must be non-negative: {cell!r}")
        position = (cell["row"], cell["col"])
        if position in seen:
            raise ValueError(f"Duplicate cell at row={position[0]}, col={position[1]}")
        seen.add(position)

    derived_rows = max(c["row"] for c in cells) + 1
    derived_cols = max(c["col"] for c in cells) + 1
    rows = table.get("rows", derived_rows)
    cols = table.get("cols", derived_cols)

    if rows != derived_rows or cols != derived_cols:
        raise ValueError(
            f"Declared grid {rows}x{cols} disagrees with the cells, which span "
            f"{derived_rows}x{derived_cols}. Fix one or omit 'rows'/'cols' to "
            f"derive them."
        )
    if len(seen) != rows * cols:
        missing = [(r, c) for r in range(rows) for c in range(cols) if (r, c) not in seen]
        raise ValueError(
            f"Grid is {rows}x{cols} ({rows * cols} cells) but only {len(seen)} were "
            f"given — missing {missing[:8]}{' ...' if len(missing) > 8 else ''}. A "
            f"reference grid must be dense; write an empty cell as "
            f'{{"row": r, "col": c, "text": ""}} rather than omitting it, so '
            f"'the answer is blank here' is distinguishable from 'nobody wrote "
            f"this cell down'."
        )

    has_header = table.get("has_header")
    if not isinstance(has_header, bool):
        raise ValueError(
            "Reference table must declare 'has_header' explicitly (true/false). "
            "core/table_evaluator.py aligns columns by header text when both "
            "sides have one, so this is not safe to guess on the reference side."
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "rows": rows,
        "cols": cols,
        "has_header": has_header,
        "cells": [{"row": c["row"], "col": c["col"], "text": c["text"]} for c in cells],
    }


def load_reference_table(cur, table: dict, question_id: str | None = None,
                          variant_id: str | None = None,
                          blob_url: str | None = None) -> str:
    """Validates the grid, then inserts one content_assets row
    (asset_type='table') holding it, optionally linked via
    question_asset_links — the write, and the blob_url check, are
    core/reference_assets.py's. Caller owns the transaction. Returns the new
    asset_id."""
    structured_data = _validate_table(table)
    return reference_assets.insert_reference_asset(
        cur, "table", structured_data,
        question_id=question_id, variant_id=variant_id, blob_url=blob_url,
    )


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("json_path", type=Path)
    ap.add_argument("--question-id", default=None,
                    help="link as role='question_source' to this question")
    ap.add_argument("--variant-id", default=None,
                    help="link as role='answer_component' to this reference_answer_variant")
    ap.add_argument("--blob-url", default=None,
                    help='stable "bucket/key" reference to the source image '
                         "(never a presigned URL)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the result, roll back instead of committing")
    args = ap.parse_args()

    if args.question_id and args.variant_id:
        ap.error("pass at most one of --question-id / --variant-id")

    table = json.loads(args.json_path.read_text())

    try:
        with db_mod.transaction(dry_run=args.dry_run) as cur:
            asset_id = load_reference_table(
                cur, table, question_id=args.question_id,
                variant_id=args.variant_id, blob_url=args.blob_url,
            )
            rows = table.get("rows") or max(c["row"] for c in table["cells"]) + 1
            cols = table.get("cols") or max(c["col"] for c in table["cells"]) + 1
            print(f"Loaded reference table -> asset_id={asset_id} "
                  f"({rows} rows x {cols} cols, has_header={table.get('has_header')})")

        if args.dry_run:
            print("[dry-run] rolled back, no changes persisted.")
        else:
            print("Committed.")
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
