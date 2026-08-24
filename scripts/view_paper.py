#!/usr/bin/env python3
"""
scripts/view_paper.py — read-only display of generated papers
====================================================================
Lists generated papers or shows the full detail of one paper, including
which questions were assigned to which slots.

Same pattern as view_pattern.py — never writes to the DB.

Usage:
    export PGHOST=... PGDATABASE=... PGUSER=... PGPASSWORD=...

    # List all generated papers
    python3 scripts/view_paper.py --list

    # Show full detail of one paper
    python3 scripts/view_paper.py --paper-id <uuid>
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db as db_mod  # noqa: E402


_SQL_LIST = """
    SELECT gp.paper_id, gp.name, gp.status, gp.generated_at,
           pp.name AS pattern_name, pp.total_marks,
           gp.generated_by
    FROM   generated_papers gp
    JOIN   paper_patterns   pp ON pp.pattern_id = gp.pattern_id
    ORDER  BY gp.generated_at DESC
"""

_SQL_PAPER_META = """
    SELECT gp.paper_id, gp.name, gp.status, gp.generated_at,
           gp.generated_by,
           pp.pattern_id, pp.name AS pattern_name, pp.total_marks
    FROM   generated_papers gp
    JOIN   paper_patterns   pp ON pp.pattern_id = gp.pattern_id
    WHERE  gp.paper_id = %s
"""

_SQL_PAPER_TREE = """
    SELECT
        psec.paper_section_id,
        ps.section_order, ps.section_label, ps.is_mandatory,
        psec.choose_count,
        psl.slot_id, psl.slot_label, psl.slot_order, psl.marks, psl.style,
        psl.parent_slot_id,
        pq.question_id,
        q.content   AS question_content,
        q.marks_max AS question_marks
    FROM   paper_sections    psec
    JOIN   pattern_sections  ps   ON ps.section_id = psec.section_id
    JOIN   pattern_slots     psl  ON psl.section_id = ps.section_id
    LEFT JOIN paper_questions pq  ON pq.paper_section_id = psec.paper_section_id
                                 AND pq.slot_id = psl.slot_id
    LEFT JOIN questions       q   ON q.question_id = pq.question_id
    WHERE  psec.paper_id = %s
    ORDER  BY ps.section_order, psl.parent_slot_id NULLS FIRST, psl.slot_order
"""


def list_papers(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_SQL_LIST)
        rows = cur.fetchall()

    if not rows:
        print("No generated papers found.")
        return

    print(f"{'PAPER_ID':<38} {'NAME':<35} {'STATUS':<10} {'PATTERN':<30} {'MARKS':>6}  GENERATED")
    for paper_id, name, status, generated_at, pattern_name, total_marks, generated_by in rows:
        print(f"{str(paper_id):<38} {name[:33]:<35} {status:<10} "
              f"{pattern_name[:28]:<30} {total_marks:>6.1f}  {generated_at}")


def show_paper(conn, paper_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(_SQL_PAPER_META, (paper_id,))
        meta = cur.fetchone()
        if meta is None:
            print(f"No paper found with id {paper_id}", file=sys.stderr)
            sys.exit(1)

        (pid, name, status, generated_at, generated_by,
         pattern_id, pattern_name, total_marks) = meta

        cur.execute(_SQL_PAPER_TREE, (paper_id,))
        rows = cur.fetchall()

    owner = "system" if generated_by is None else str(generated_by)[:8]
    print(f"\n{name}")
    print(f"  paper_id     : {pid}")
    print(f"  pattern      : {pattern_name} ({pattern_id})")
    print(f"  status       : {status}")
    print(f"  total_marks  : {total_marks}")
    print(f"  generated_at : {generated_at}")
    print(f"  generated_by : {owner}")
    print()

    # Build section tree
    sections = {}
    slots_by_id = {}

    for (paper_section_id, sec_order, sec_label, is_mandatory, choose_count,
         slot_id, slot_label, slot_order, marks, style, parent_id,
         question_id, question_content, question_marks) in rows:

        sec_key = sec_order
        sections.setdefault(sec_key, {
            "label": sec_label,
            "mandatory": is_mandatory,
            "choose_count": choose_count,
            "top_slots": [],
        })
        slot_rec = {
            "slot_id": str(slot_id),
            "label": slot_label,
            "order": slot_order,
            "marks": marks,
            "style": style,
            "question_id": str(question_id) if question_id else None,
            "question_content": question_content,
            "question_marks": question_marks,
            "children": [],
        }
        slots_by_id[str(slot_id)] = slot_rec
        if parent_id is None:
            sections[sec_key]["top_slots"].append(slot_rec)
        else:
            parent_key = str(parent_id)
            if parent_key in slots_by_id:
                slots_by_id[parent_key]["children"].append(slot_rec)

    filled = 0
    total = 0

    for sec_order in sorted(sections):
        sec = sections[sec_order]
        if sec["mandatory"]:
            tag = "mandatory"
        else:
            tag = f"optional, choose {sec['choose_count']}"
        print(f"  Section {sec_order}: {sec['label']}  [{tag}]")

        for slot in sec["top_slots"]:
            if slot["children"]:
                # Parent slot
                print(f"    {slot['label']:<6} {slot['marks']:>5}M  ({slot['style']})  [parent]")
                for child in slot["children"]:
                    _print_slot_detail(child, indent=6)
                    total += 1
                    if child["question_id"]:
                        filled += 1
            else:
                # Leaf slot
                _print_slot_detail(slot, indent=4)
                total += 1
                if slot["question_id"]:
                    filled += 1
        print()

    print(f"  Filled: {filled}/{total} slots")
    if filled < total:
        print(f"  ⚠ {total - filled} slot(s) have no assigned question.")
    print()


def _print_slot_detail(slot: dict, indent: int = 4) -> None:
    prefix = " " * indent
    if slot["question_id"]:
        content = slot["question_content"] or ""
        if len(content) > 70:
            content = content[:67] + "..."
        marks_note = ""
        if slot["question_marks"] and slot["question_marks"] != slot["marks"]:
            marks_note = f"  [actual: {slot['question_marks']}M]"
        print(f"{prefix}{slot['label']:<6} {slot['marks']:>5}M  ({slot['style']})"
              f"  ← {slot['question_id'][:8]}...{marks_note}")
        print(f"{prefix}       {content!r}")
    else:
        print(f"{prefix}{slot['label']:<6} {slot['marks']:>5}M  ({slot['style']})"
              f"  ← [EMPTY]")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true",
                       help="list all generated papers")
    group.add_argument("--paper-id", metavar="UUID",
                       help="show full detail of one paper")
    args = ap.parse_args()

    conn = db_mod.get_connection()
    try:
        if args.list:
            list_papers(conn)
        else:
            show_paper(conn, args.paper_id)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
