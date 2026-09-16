#!/usr/bin/env python3
"""
scripts/load_content.py — CLI front door onto core/paragraphs.py: turn a
block of text into `paragraphs` (+ `sentences`) rows.

WHY THIS EXISTS: api/ and scripts/ are two front doors onto one core/
library (CLAUDE_CONTEXT.md §11 rule 1). api/routers/content.py calls
core/paragraphs.py directly; this script is the CLI side of that same
symmetry, and the one already-written way to load content from a terminal
without `psql` — before this script, and before api/routers/content.py,
tests/test_api/conftest.py::make_paragraph's own docstring notes it was the
ONLY place in the repo that ever inserted a paragraph.

Splits on blank lines by default (core.paragraphs.split_into_paragraphs) —
pass --no-split to load the input as a single paragraph instead. There is no
LLM call anywhere in this path, so there is no --stub-llm; --dry-run is the
only flag that changes what gets committed.

Usage:
    python3 scripts/load_content.py --source-document "Unit 3 notes" --file notes.txt
    cat notes.txt | python3 scripts/load_content.py --source-document "Unit 3 notes"
    python3 scripts/load_content.py --source-document "Unit 3 notes" --file notes.txt --no-split
    python3 scripts/load_content.py --source-document "Unit 3 notes" --file notes.txt --dry-run
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db as db_mod           # noqa: E402
from core import paragraphs as paragraphs_lib  # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--source-document", required=True,
                     help="name for this upload, e.g. 'Unit 3 notes' — every "
                          "paragraph created carries it, and it is the "
                          "picker's document filter value")
    ap.add_argument("--file", default=None,
                     help="path to a .txt file to load (default: read stdin)")
    ap.add_argument("--no-split", action="store_true",
                     help="load the whole input as ONE paragraph instead of "
                          "splitting on blank lines")
    ap.add_argument("--dry-run", action="store_true",
                     help="print what would be created, roll back instead "
                          "of committing")
    args = ap.parse_args()

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()

    if args.no_split:
        contents = [text.strip()] if text.strip() else []
    else:
        contents = paragraphs_lib.split_into_paragraphs(text)

    if not contents:
        print("ERROR: no non-blank content to load.", file=sys.stderr)
        sys.exit(1)

    try:
        with db_mod.transaction(dry_run=args.dry_run) as cur:
            created = paragraphs_lib.create_paragraphs(
                cur, source_document=args.source_document, contents=contents,
            )
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Created {len(created)} paragraph(s) from {args.source_document!r}:\n")
    for p in created:
        preview = p["content"][:60] + ("…" if len(p["content"]) > 60 else "")
        print(f"  [{p['paragraph_id']}] ({p['sentence_count']} sentence(s))")
        print(f"    {preview!r}")

    if args.dry_run:
        print("\n[dry-run] rolled back, no changes persisted.")
    else:
        print("\nCommitted. All paragraphs are status='active' — ready to "
              "generate questions from.")


if __name__ == "__main__":
    sys.exit(main())
