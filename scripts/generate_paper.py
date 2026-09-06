#!/usr/bin/env python3
"""
scripts/generate_paper.py — generate a concrete exam paper from a pattern
====================================================================
Takes a paper pattern (already loaded via load_paper_pattern.py) and fills
its slots with actual live questions from the bank.

Auto-select matching (v1):
  - Matches by style (exact) + marks_max (exact, then ±tolerance).
  - Excludes questions already assigned to this paper (no repeats).
  - Randomised selection for variety.

Mandatory section slots that can't be filled → hard error (paper invalid).
Optional section slots that can't be filled → warning (degraded but valid).

The generated paper is stored in the DB (generated_papers / paper_sections /
paper_questions tables from migration 008). Optionally dumps to a JSON file
for inspection or handoff to a future renderer.

Written as a plain function so a future API endpoint can call it directly.

Usage:
    export PGHOST=... PGDATABASE=... PGUSER=... PGPASSWORD=...

    # Generate a paper (auto-select questions)
    python3 scripts/generate_paper.py <pattern_id> --name "AIML CIA-2 Aug 2026"

    # With a teacher as owner
    python3 scripts/generate_paper.py <pattern_id> --name "..." \\
        --generated-by 44444444-4444-4444-4444-444444444444

    # Custom choose_count for optional sections (default: 1)
    python3 scripts/generate_paper.py <pattern_id> --name "..." --choose-count 2

    # Dump the result to a JSON file
    python3 scripts/generate_paper.py <pattern_id> --name "..." --output paper.json

    # Show the filled paper tree
    python3 scripts/generate_paper.py <pattern_id> --name "..." --show

    # Dry run — inspect without persisting
    python3 scripts/generate_paper.py <pattern_id> --name "..." --dry-run --show
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db as db_mod           # noqa: E402
from core.paper_generator import generate_paper  # noqa: E402


def _print_paper(result: dict) -> None:
    """Pretty-print the generated paper as a tree."""
    print(f"\n{'=' * 60}")
    print(f"  {result['name']}")
    print(f"  Pattern: {result['pattern_name']}")
    print(f"  Status:  {result['status']}")
    print(f"  Filled:  {result['total_filled']}/{result['total_slots']} slots, "
          f"{result['filled_marks']}/{result['pattern_total_marks']} marks"
          f"{'' if result['is_complete'] else '  [INCOMPLETE]'}")
    print(f"{'=' * 60}\n")

    for section in result["sections"]:
        tag = "mandatory" if section["is_mandatory"] else f"optional, choose {section['choose_count']}"
        print(f"  Section {section['section_order']}: {section['section_label']}  [{tag}]")

        for top_slot in section["slots"]:
            if top_slot["is_parent"]:
                # Parent slot header
                print(f"    {top_slot['slot_label']:<6} {top_slot['marks']:>5}M  ({top_slot['style']})  [parent]")
                for leaf in top_slot["leaves"]:
                    _print_slot(leaf, indent=6)
            else:
                # Standalone leaf slot
                leaf = top_slot["leaves"][0]
                _print_slot(leaf, indent=4)
        print()

    if result["warnings"]:
        # Structured {slot_label, section, marks, reason} objects, not prose
        # (Hardening pass 2026-09-06) — formatted here for a human reading
        # the CLI's output; api/schemas/papers.py::SlotWarning is what a
        # client of the API actually gets.
        print("  ⚠ Warnings:")
        for w in result["warnings"]:
            print(f"    {w['section']} / {w['slot_label']} ({w['marks']}M): {w['reason']}")
        print()


def _print_slot(leaf: dict, indent: int = 4) -> None:
    prefix = " " * indent
    if leaf["assigned"]:
        marks_note = ""
        if leaf["question_marks"] != leaf["marks"]:
            marks_note = f"  [actual: {leaf['question_marks']}M]"
        # Truncate question content for display
        content = leaf["question_content"] or ""
        if len(content) > 70:
            content = content[:67] + "..."
        print(f"{prefix}{leaf['slot_label']:<6} {leaf['marks']:>5}M  ({leaf['style']})"
              f"  ← {leaf['question_id'][:8]}...{marks_note}")
        print(f"{prefix}       {content!r}")
    else:
        print(f"{prefix}{leaf['slot_label']:<6} {leaf['marks']:>5}M  ({leaf['style']})"
              f"  ← [EMPTY — no matching question]")


def _make_json_serializable(result: dict) -> dict:
    """Ensure the result dict is JSON-serializable (handle any UUID/datetime)."""
    # The result from generate_paper is already strings, but be safe
    return json.loads(json.dumps(result, default=str))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("pattern_id",
                    help="UUID of the paper pattern to fill")
    ap.add_argument("--name", required=True,
                    help="human-readable paper name (e.g. 'AIML CIA-2 Aug 2026')")
    ap.add_argument("--generated-by", default=None,
                    help="reviewer UUID of the teacher creating this paper "
                         "(default: NULL = system-generated)")
    ap.add_argument("--choose-count", type=int, default=1,
                    help="default choose_count for optional sections (default: 1)")
    ap.add_argument("--marks-tolerance", type=float, default=1.0,
                    help="allowed ± on marks_max for fuzzy matching (default: 1.0)")
    ap.add_argument("--output", metavar="FILE", default=None,
                    help="dump the generated paper to a JSON file")
    ap.add_argument("--show", action="store_true",
                    help="print the filled paper tree to stdout")
    ap.add_argument("--dry-run", action="store_true",
                    help="roll back instead of committing — inspect without persisting")
    args = ap.parse_args()

    try:
        with db_mod.transaction(dry_run=args.dry_run) as cur:
            result = generate_paper(
                cur,
                pattern_id=args.pattern_id,
                name=args.name,
                generated_by=args.generated_by,
                choose_count=args.choose_count,
                marks_tolerance=args.marks_tolerance,
            )

        if args.show:
            _print_paper(result)

        if args.output:
            serializable = _make_json_serializable(result)
            with open(args.output, "w") as f:
                json.dump(serializable, f, indent=2)
            print(f"Paper JSON written to {args.output}")

        if args.dry_run:
            print(f"[dry-run] rolled back. Paper would be: {result['paper_id']}")
        else:
            print(f"Generated paper '{result['name']}' → {result['paper_id']}")
            print(f"  Filled {result['total_filled']}/{result['total_slots']} slots.")
            if result["warnings"]:
                print(f"  ⚠ {len(result['warnings'])} warning(s) — run with --show for details.")

    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
