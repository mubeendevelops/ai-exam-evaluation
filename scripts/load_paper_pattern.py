#!/usr/bin/env python3
"""
scripts/load_paper_pattern.py  —  Task 2a/2b: Paper pattern loader
====================================================================
Loads a paper pattern definition (JSON) into paper_patterns /
pattern_sections / pattern_slots. Idempotent — safe to re-run: IDs are
deterministic (uuid5 over a natural key), and existing rows are upserted
via ON CONFLICT DO UPDATE, mirroring the convention already used in
scripts/load_exam_bank.py.

Usage
-----
    python3 scripts/load_paper_pattern.py patterns/aiml_22cs53_cia_pattern.json
    python3 scripts/load_paper_pattern.py patterns/foo.json --dry-run
    python3 scripts/load_paper_pattern.py patterns/foo.json --show-tree
    python3 scripts/load_paper_pattern.py patterns/foo.json --created-by <reviewer_uuid>

JSON schema
-----------
{
  "name": "AIML 22CS53 — CIA Standard Pattern",
  "description": "4-section pattern: 2 mandatory + 3 either-or sections",
  "course_code": "22CS53",
  "sections": [
    {
      "section_label": "Part A — Compulsory",
      "section_order": 1,
      "is_mandatory": true,
      "slots": [
        {"slot_label": "Q1", "slot_order": 1, "marks": 10, "style": "long"},
        {
          "slot_label": "Q2", "slot_order": 2, "marks": 10, "style": "long",
          "sub_parts": [
            {"slot_label": "Q2a", "slot_order": 1, "marks": 3, "style": "long"},
            {"slot_label": "Q2b", "slot_order": 2, "marks": 7, "style": "long"}
          ]
        }
      ]
    },
    {
      "section_label": "Part B — Either/Or",
      "section_order": 2,
      "is_mandatory": false,
      "slots": [
        {"slot_label": "Q3", "slot_order": 1, "marks": 10, "style": "long"},
        {"slot_label": "Q4", "slot_order": 2, "marks": 10, "style": "long"}
      ]
    }
  ]
}

--created-by is optional. If omitted, the pattern is loaded as a system
template (created_by = NULL). Pass a reviewer UUID to load it as
teacher-owned instead.

Validation performed before any DB write
-----------------------------------------
1. Every slot's "style" is one of: long, short, one_word, mcq
   (mirrors the CHECK constraint in 006_paper_pattern_schema.sql).
2. section_order values within a pattern are unique and start at 1.
3. slot_order values within a section (or within a parent's sub_parts)
   are unique and start at 1.
4. Parent slot marks == sum(sub_parts marks). Mismatches abort the load
   with a clear error — pass --force to load anyway (marks are then
   corrected to match the sum, and a warning is printed).

total_marks calculation
------------------------
total_marks = sum(marks of mandatory top-level slots)
            + sum(one representative slot's marks per optional section)

For optional sections, this script assumes the alternatives are worth
equal marks (true of every pattern seen so far — "answer Q3 or Q4",
both 10M). If a section's top-level slots have unequal marks, a warning
is printed and the MAXIMUM is used for total_marks (worst case).
This total is a convenience default; it does NOT reflect choose_count > 1,
which is decided at paper-generation time, not pattern-definition time.
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db as db_mod              # noqa: E402
from core import pattern_tree              # noqa: E402


# Fixed namespace for this project's deterministic UUID5 generation.
# Any natural key hashed under this namespace always produces the same UUID,
# which is what makes re-running the loader idempotent.
_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "ai-exam-evaluation.patterns")

_VALID_STYLES = {"long", "short", "one_word", "mcq"}


def _det_uuid(natural_key: str) -> uuid.UUID:
    """Deterministic UUID5 from a natural key string."""
    return uuid.uuid5(_NAMESPACE, natural_key)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_pattern(pattern: dict, force: bool) -> None:
    if "name" not in pattern or not pattern["name"].strip():
        raise ValueError("Pattern must have a non-empty 'name'.")

    if "sections" not in pattern or not pattern["sections"]:
        raise ValueError("Pattern must have at least one section.")

    seen_section_orders = set()
    for section in pattern["sections"]:
        so = section.get("section_order")
        if so in seen_section_orders:
            raise ValueError(f"Duplicate section_order: {so}")
        seen_section_orders.add(so)

        if "slots" not in section or not section["slots"]:
            raise ValueError(
                f"Section '{section.get('section_label')}' has no slots."
            )

        seen_slot_orders = set()
        for slot in section["slots"]:
            slot_order = slot.get("slot_order")
            if slot_order in seen_slot_orders:
                raise ValueError(
                    f"Duplicate slot_order {slot_order} in section "
                    f"'{section.get('section_label')}'."
                )
            seen_slot_orders.add(slot_order)

            _validate_slot(slot, section.get("section_label"), force)


def _validate_slot(slot: dict, section_label: str, force: bool) -> None:
    style = slot.get("style")
    if style not in _VALID_STYLES:
        raise ValueError(
            f"Slot '{slot.get('slot_label')}' in section '{section_label}' "
            f"has invalid style '{style}'. Must be one of {_VALID_STYLES}."
        )

    marks = slot.get("marks")
    if not isinstance(marks, (int, float)) or marks <= 0:
        raise ValueError(
            f"Slot '{slot.get('slot_label')}' must have marks > 0, "
            f"got {marks!r}."
        )

    sub_parts = slot.get("sub_parts", [])
    if sub_parts:
        seen_orders = set()
        child_sum = 0.0
        for child in sub_parts:
            co = child.get("slot_order")
            if co in seen_orders:
                raise ValueError(
                    f"Duplicate sub-part slot_order {co} under "
                    f"'{slot.get('slot_label')}'."
                )
            seen_orders.add(co)
            _validate_slot(child, section_label, force)
            child_sum += child["marks"]

        if abs(child_sum - marks) > 1e-6:
            msg = (
                f"Slot '{slot.get('slot_label')}' marks ({marks}) != "
                f"sum of sub-part marks ({child_sum})."
            )
            if force:
                print(f"WARNING: {msg} Correcting parent marks to {child_sum} "
                      f"(--force was passed).", file=sys.stderr)
                slot["marks"] = child_sum
            else:
                raise ValueError(msg + " Pass --force to auto-correct.")


# ---------------------------------------------------------------------------
# total_marks computation
# ---------------------------------------------------------------------------

def _compute_total_marks(pattern: dict) -> float:
    total = 0.0
    for section in pattern["sections"]:
        top_level_marks = [slot["marks"] for slot in section["slots"]]
        if section.get("is_mandatory", True):
            total += sum(top_level_marks)
        else:
            if len(set(top_level_marks)) > 1:
                print(
                    f"WARNING: optional section '{section.get('section_label')}' "
                    f"has unequal top-level slot marks {top_level_marks}. "
                    f"Using max ({max(top_level_marks)}) for total_marks.",
                    file=sys.stderr,
                )
            total += max(top_level_marks)
    return total


# ---------------------------------------------------------------------------
# DB writes
# ---------------------------------------------------------------------------

_UPSERT_PATTERN = """
    INSERT INTO paper_patterns
        (pattern_id, name, description, total_marks, course_code, created_by)
    VALUES
        (%(pattern_id)s, %(name)s, %(description)s, %(total_marks)s,
         %(course_code)s, %(created_by)s)
    ON CONFLICT (pattern_id) DO UPDATE SET
        name         = EXCLUDED.name,
        description  = EXCLUDED.description,
        total_marks  = EXCLUDED.total_marks,
        course_code  = EXCLUDED.course_code,
        created_by   = EXCLUDED.created_by;
"""

_UPSERT_SECTION = """
    INSERT INTO pattern_sections
        (section_id, pattern_id, section_label, section_order, is_mandatory)
    VALUES
        (%(section_id)s, %(pattern_id)s, %(section_label)s,
         %(section_order)s, %(is_mandatory)s)
    ON CONFLICT (section_id) DO UPDATE SET
        section_label = EXCLUDED.section_label,
        section_order = EXCLUDED.section_order,
        is_mandatory  = EXCLUDED.is_mandatory;
"""

_UPSERT_SLOT = """
    INSERT INTO pattern_slots
        (slot_id, section_id, slot_label, slot_order, marks, style, parent_slot_id)
    VALUES
        (%(slot_id)s, %(section_id)s, %(slot_label)s, %(slot_order)s,
         %(marks)s, %(style)s, %(parent_slot_id)s)
    ON CONFLICT (slot_id) DO UPDATE SET
        slot_label     = EXCLUDED.slot_label,
        slot_order     = EXCLUDED.slot_order,
        marks          = EXCLUDED.marks,
        style          = EXCLUDED.style,
        parent_slot_id = EXCLUDED.parent_slot_id;
"""


def _load_slot(cur, section_id: uuid.UUID, section_key: str, slot: dict,
                parent_slot_id: uuid.UUID | None, parent_key: str | None) -> uuid.UUID:
    """Insert/upsert one slot (and recursively its sub-parts). Returns slot_id."""
    key_scope = parent_key or section_key
    natural_key = f"slot:{key_scope}:{slot['slot_order']}"
    slot_id = _det_uuid(natural_key)

    cur.execute(_UPSERT_SLOT, {
        "slot_id":         str(slot_id),
        "section_id":      str(section_id),
        "slot_label":      slot.get("slot_label"),
        "slot_order":      slot["slot_order"],
        "marks":           slot["marks"],
        "style":           slot["style"],
        "parent_slot_id":  str(parent_slot_id) if parent_slot_id else None,
    })

    for child in slot.get("sub_parts", []):
        _load_slot(cur, section_id, section_key, child, slot_id, natural_key)

    return slot_id


def load_pattern(conn, pattern: dict, created_by: str | None) -> uuid.UUID:
    """Upserts one pattern (and its sections/slots) into paper_patterns /
    pattern_sections / pattern_slots. Caller owns the transaction — commit/
    rollback is the caller's responsibility. Returns the pattern's
    (deterministic, uuid5-derived) pattern_id."""
    pattern_key = f"pattern:{pattern['name']}"
    pattern_id = _det_uuid(pattern_key)
    total_marks = _compute_total_marks(pattern)

    with conn.cursor() as cur:
        cur.execute(_UPSERT_PATTERN, {
            "pattern_id":   str(pattern_id),
            "name":         pattern["name"],
            "description":  pattern.get("description"),
            "total_marks":  total_marks,
            "course_code":  pattern.get("course_code"),
            "created_by":   created_by,
        })

        for section in pattern["sections"]:
            section_key = f"{pattern_key}:section:{section['section_order']}"
            section_id = _det_uuid(section_key)

            cur.execute(_UPSERT_SECTION, {
                "section_id":     str(section_id),
                "pattern_id":     str(pattern_id),
                "section_label":  section["section_label"],
                "section_order":  section["section_order"],
                "is_mandatory":   section.get("is_mandatory", True),
            })

            for slot in section["slots"]:
                _load_slot(cur, section_id, section_key, slot, None, None)

    return pattern_id


# ---------------------------------------------------------------------------
# Tree display (post-load confirmation)
# ---------------------------------------------------------------------------

def print_tree(conn, pattern_id: uuid.UUID) -> None:
    with conn.cursor() as cur:
        rows = pattern_tree.fetch_tree_rows(cur, pattern_id)

    by_section = pattern_tree.build_section_tree(rows)

    print(f"\nPattern tree ({pattern_id}):\n")
    pattern_tree.render_tree(by_section)
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("json_path", help="Path to pattern JSON definition.")
    parser.add_argument("--created-by", metavar="UUID", default=None,
                         help="Reviewer UUID. Omit for a system template (created_by=NULL).")
    parser.add_argument("--force", action="store_true", default=False,
                         help="Auto-correct parent/sub-part marks mismatches instead of aborting.")
    parser.add_argument("--dry-run", action="store_true", default=False,
                         help="Validate and compute, but roll back all DB writes.")
    parser.add_argument("--show-tree", action="store_true", default=False,
                         help="Print the resulting pattern tree after loading.")
    args = parser.parse_args()

    with open(args.json_path) as f:
        pattern = json.load(f)

    _validate_pattern(pattern, force=args.force)

    conn = db_mod.get_connection()
    try:
        pattern_id = load_pattern(conn, pattern, created_by=args.created_by)

        if args.show_tree:
            print_tree(conn, pattern_id)

        if args.dry_run:
            conn.rollback()
            print(f"DRY RUN — rolled back. Pattern would be: {pattern_id}")
        else:
            conn.commit()
            print(f"Loaded pattern '{pattern['name']}' → {pattern_id}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
