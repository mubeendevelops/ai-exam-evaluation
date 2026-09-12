"""
core/pattern_tree.py — shared section/slot tree fetch + render for a paper
pattern, used identically by scripts/load_paper_pattern.py (--show-tree)
and scripts/view_pattern.py (--pattern-id / --name). Both scripts previously
duplicated the same query and tree-nesting loop in full.

`nest_slots` below is the one place flat (section, slot) rows become a
nested tree. scripts/view_paper.py and core/paper_generator.py use it too:
they carry extra columns (choose_count, the assigned question) and their own
accounting/rendering, but the NESTING is identical, and each kept its own
record shape by passing its own dicts in. Those two used to hold their own
copies of this loop (CLEANUP_AUDIT.md §2).
"""
from __future__ import annotations

SQL_PATTERN_TREE = """
    SELECT
        ps.section_order, ps.section_label, ps.is_mandatory,
        psl.slot_id, psl.slot_label, psl.slot_order, psl.marks, psl.style,
        psl.parent_slot_id
    FROM   pattern_sections ps
    JOIN   pattern_slots    psl ON psl.section_id = ps.section_id
    WHERE  ps.pattern_id = %(pattern_id)s
    ORDER  BY ps.section_order, psl.parent_slot_id NULLS FIRST, psl.slot_order;
"""


def fetch_tree_rows(cur, pattern_id) -> list[tuple]:
    cur.execute(SQL_PATTERN_TREE, {"pattern_id": str(pattern_id)})
    return cur.fetchall()


def nest_slots(items) -> dict:
    """Nests flat slot rows under their sections, at any depth.

    `items` is an iterable of
    (section_key, section_fields, slot_id, parent_slot_id, slot_rec) — the
    caller builds its OWN section_fields and slot_rec dicts, so each call
    site keeps its own key names and extra columns. Returns
    {section_key: {**section_fields, "top_slots": [...]}}; every slot_rec
    gains a "children" list, and children keep their row order.

    TWO PASSES — index every slot, then attach — so ROW ORDER CANNOT LOSE A
    SLOT. The query orders by `parent_slot_id NULLS FIRST`, which puts
    top-level slots first but says nothing about the order of a sub-part
    versus a sub-SUB-part (Q2a vs Q2a(i)): those sort by their parents'
    UUIDs. The one-pass version each call site used to carry either dropped
    a grandchild that arrived before its parent (leaving its parent looking
    like a leaf, so core/paper_generator.py would assign IT a question and
    silently omit the sub-part) or raised KeyError. Nothing in the schema
    caps nesting depth — migration 006 only requires a parent in the same
    section — and scripts/load_paper_pattern.py loads `sub_parts`
    recursively, so depth > 1 is a supported shape, not a hypothetical.

    A parent_slot_id naming a slot that is not in `items` raises ValueError:
    the FK plus trg_slot_parent_same_section make it unreachable from one
    pattern's rows, and silently dropping the slot is what this function
    exists to stop.
    """
    by_section: dict = {}
    slots_by_id: dict[str, dict] = {}
    parented: list[tuple[str, dict]] = []

    for section_key, section_fields, slot_id, parent_slot_id, slot_rec in items:
        section = by_section.setdefault(section_key, {**section_fields, "top_slots": []})
        slot_rec.setdefault("children", [])
        slots_by_id[str(slot_id)] = slot_rec
        if parent_slot_id is None:
            section["top_slots"].append(slot_rec)
        else:
            parented.append((str(parent_slot_id), slot_rec))

    for parent_key, slot_rec in parented:
        parent = slots_by_id.get(parent_key)
        if parent is None:
            raise ValueError(
                f"slot {slot_rec} names parent_slot_id {parent_key}, which is not "
                f"among these rows — a slot must never be silently dropped"
            )
        parent["children"].append(slot_rec)

    return by_section


def build_section_tree(rows: list[tuple]) -> dict[int, dict]:
    """Nests flat (section, slot) rows into {section_order: {label,
    mandatory, top_slots: [{..., children: [...]}]}}."""
    return nest_slots(
        (
            sec_order,
            {"label": sec_label, "mandatory": is_mandatory},
            slot_id,
            parent_id,
            {"id": str(slot_id), "label": slot_label, "order": slot_order,
             "marks": marks, "style": style, "children": []},
        )
        for (sec_order, sec_label, is_mandatory, slot_id, slot_label,
             slot_order, marks, style, parent_id) in rows
    )


def render_tree(by_section: dict[int, dict], optional_tag: str = "optional") -> None:
    """Prints the nested tree in the shared display format:
        Section N: label  [mandatory|<optional_tag>]
          SlotLabel  MarksM  (style)
            ChildLabel  MarksM  (style)

    optional_tag lets callers keep their own wording for non-mandatory
    sections (e.g. view_pattern.py's "optional (choose_count set at
    generation)" vs. load_paper_pattern.py's plain "optional").
    """
    for sec_order in sorted(by_section):
        sec = by_section[sec_order]
        tag = "mandatory" if sec["mandatory"] else optional_tag
        print(f"  Section {sec_order}: {sec['label']}  [{tag}]")
        for slot in sec["top_slots"]:
            print(f"    {slot['label']:<6} {slot['marks']:>5}M  ({slot['style']})")
            for child in slot["children"]:
                print(f"      {child['label']:<6} {child['marks']:>5}M  ({child['style']})")
