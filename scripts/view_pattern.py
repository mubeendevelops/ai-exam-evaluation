#!/usr/bin/env python3
"""
scripts/view_pattern.py  —  Task 2a/2b: Paper pattern viewer
====================================================================
Read-only display of a stored paper pattern as a nested section/slot tree.
Useful for verifying a pattern after scripts/load_paper_pattern.py, or for
browsing what patterns exist.

Usage
-----
    # List all active patterns:
    python3 scripts/view_pattern.py --list

    # Show a specific pattern's tree by ID:
    python3 scripts/view_pattern.py --pattern-id <uuid>

    # Show a specific pattern's tree by exact name:
    python3 scripts/view_pattern.py --name "AIML 22CS53 — CIA Standard Pattern"
"""

import argparse
import sys
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from core.db import get_connection  # noqa: E402


_SQL_LIST = """
    SELECT pattern_id, name, course_code, total_marks, is_active, created_by
    FROM   paper_patterns
    ORDER  BY created_at DESC;
"""

_SQL_PATTERN_BY_NAME = """
    SELECT pattern_id FROM paper_patterns WHERE name = %(name)s;
"""

_SQL_TREE = """
    SELECT
        ps.section_order, ps.section_label, ps.is_mandatory,
        psl.slot_id, psl.slot_label, psl.slot_order, psl.marks, psl.style,
        psl.parent_slot_id
    FROM   pattern_sections ps
    JOIN   pattern_slots    psl ON psl.section_id = ps.section_id
    WHERE  ps.pattern_id = %(pattern_id)s
    ORDER  BY ps.section_order, psl.parent_slot_id NULLS FIRST, psl.slot_order;
"""

_SQL_PATTERN_META = """
    SELECT name, description, course_code, total_marks, is_active, created_by
    FROM   paper_patterns
    WHERE  pattern_id = %(pattern_id)s;
"""


def list_patterns(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_SQL_LIST)
        rows = cur.fetchall()

    if not rows:
        print("No patterns found.")
        return

    print(f"{'PATTERN_ID':<38} {'NAME':<40} {'COURSE':<10} {'MARKS':>6}  ACTIVE  OWNER")
    for pattern_id, name, course_code, total_marks, is_active, created_by in rows:
        owner = "system" if created_by is None else str(created_by)[:8]
        print(f"{str(pattern_id):<38} {name[:38]:<40} {course_code or '-':<10} "
              f"{total_marks:>6.1f}  {str(is_active):<6}  {owner}")


def print_tree(conn, pattern_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(_SQL_PATTERN_META, {"pattern_id": pattern_id})
        meta = cur.fetchone()
        if meta is None:
            print(f"No pattern found with id {pattern_id}", file=sys.stderr)
            sys.exit(1)
        name, description, course_code, total_marks, is_active, created_by = meta

        cur.execute(_SQL_TREE, {"pattern_id": pattern_id})
        rows = cur.fetchall()

    owner = "system template" if created_by is None else f"teacher ({created_by})"
    print(f"\n{name}")
    print(f"  course_code : {course_code or '-'}")
    print(f"  total_marks : {total_marks}")
    print(f"  active      : {is_active}")
    print(f"  owner       : {owner}")
    if description:
        print(f"  description : {description}")
    print()

    by_section: dict[int, dict] = {}
    slots_by_id: dict[str, dict] = {}

    for (sec_order, sec_label, is_mandatory, slot_id, slot_label,
         slot_order, marks, style, parent_id) in rows:
        by_section.setdefault(sec_order, {
            "label": sec_label, "mandatory": is_mandatory, "top_slots": []
        })
        slot_rec = {
            "id": str(slot_id), "label": slot_label, "order": slot_order,
            "marks": marks, "style": style, "children": [],
        }
        slots_by_id[str(slot_id)] = slot_rec
        if parent_id is None:
            by_section[sec_order]["top_slots"].append(slot_rec)
        else:
            slots_by_id[str(parent_id)]["children"].append(slot_rec)

    for sec_order in sorted(by_section):
        sec = by_section[sec_order]
        tag = "mandatory" if sec["mandatory"] else "optional (choose_count set at generation)"
        print(f"  Section {sec_order}: {sec['label']}  [{tag}]")
        for slot in sec["top_slots"]:
            print(f"    {slot['label']:<6} {slot['marks']:>5}M  ({slot['style']})")
            for child in slot["children"]:
                print(f"      {child['label']:<6} {child['marks']:>5}M  ({child['style']})")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="List all patterns.")
    group.add_argument("--pattern-id", metavar="UUID", help="Show tree for this pattern ID.")
    group.add_argument("--name", metavar="NAME", help="Show tree for this exact pattern name.")
    args = parser.parse_args()

    conn = get_connection()
    try:
        if args.list:
            list_patterns(conn)
        elif args.pattern_id:
            print_tree(conn, args.pattern_id)
        else:
            with conn.cursor() as cur:
                cur.execute(_SQL_PATTERN_BY_NAME, {"name": args.name})
                row = cur.fetchone()
            if row is None:
                print(f"No pattern found with name '{args.name}'", file=sys.stderr)
                sys.exit(1)
            print_tree(conn, str(row[0]))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
