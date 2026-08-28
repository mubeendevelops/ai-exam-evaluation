"""
core/pattern_tree.py — shared section/slot tree fetch + render for a paper
pattern, used identically by scripts/load_paper_pattern.py (--show-tree)
and scripts/view_pattern.py (--pattern-id / --name). Both scripts previously
duplicated the same query and tree-nesting loop in full.

scripts/view_paper.py has a similarly-shaped tree walk over *generated*
papers, but with extra columns (choose_count, assigned question_id/content)
and different accounting (filled/total slot counts) — different enough that
forcing it into this same helper would cost more clarity than it saves, so
it's left as its own implementation.
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


def build_section_tree(rows: list[tuple]) -> dict[int, dict]:
    """Nests flat (section, slot) rows into {section_order: {label,
    mandatory, top_slots: [{..., children: [...]}]}}."""
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

    return by_section


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
