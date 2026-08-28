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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db as db_mod        # noqa: E402
from core import pattern_tree        # noqa: E402


_SQL_LIST = """
    SELECT pattern_id, name, course_code, total_marks, is_active, created_by
    FROM   paper_patterns
    ORDER  BY created_at DESC;
"""

_SQL_PATTERN_BY_NAME = """
    SELECT pattern_id FROM paper_patterns WHERE name = %(name)s;
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

        rows = pattern_tree.fetch_tree_rows(cur, pattern_id)

    owner = "system template" if created_by is None else f"teacher ({created_by})"
    print(f"\n{name}")
    print(f"  course_code : {course_code or '-'}")
    print(f"  total_marks : {total_marks}")
    print(f"  active      : {is_active}")
    print(f"  owner       : {owner}")
    if description:
        print(f"  description : {description}")
    print()

    by_section = pattern_tree.build_section_tree(rows)
    pattern_tree.render_tree(by_section, optional_tag="optional (choose_count set at generation)")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="List all patterns.")
    group.add_argument("--pattern-id", metavar="UUID", help="Show tree for this pattern ID.")
    group.add_argument("--name", metavar="NAME", help="Show tree for this exact pattern name.")
    args = parser.parse_args()

    conn = db_mod.get_connection()
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
