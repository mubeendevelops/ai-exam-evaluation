"""
core/paper_generator.py — generates a concrete exam paper by assigning live
questions from the bank to the slots of a paper pattern.

This module contains the pure business logic. No CLI concerns — the script
layer (scripts/generate_paper.py) owns argparse and transaction control.

Algorithm (v1 — auto-select, style+marks matching):
  1. Fetch the pattern tree (sections → slots → sub-parts).
  2. For each LEAF slot (no children), find a matching live question:
       WHERE status = 'live' AND style = <slot.style> AND marks_max = <slot.marks>
     Exclude questions already assigned to this paper.
  3. Parent slots (with sub-parts) are structural — they are NOT assigned
     a question directly; only their leaf children are.
  4. Insert into generated_papers, paper_sections, paper_questions.
  5. Return a dict with the full paper structure.

Matching strategy:
  - Exact match on style + marks_max.
  - If no exact match: try marks_max within ±1 tolerance.
  - If still nothing: slot left empty, warning recorded.
  - Topic-aware matching (via topic_links) is a v2 enhancement.
"""

import uuid


# ---------------------------------------------------------------------------
# Pattern tree loading (reused from view_pattern.py's query shape)
# ---------------------------------------------------------------------------

_SQL_PATTERN_META = """
    SELECT name, total_marks, is_active
    FROM   paper_patterns
    WHERE  pattern_id = %s
"""

_SQL_PATTERN_TREE = """
    SELECT
        ps.section_id, ps.section_order, ps.section_label, ps.is_mandatory,
        psl.slot_id, psl.slot_label, psl.slot_order, psl.marks, psl.style,
        psl.parent_slot_id
    FROM   pattern_sections ps
    JOIN   pattern_slots    psl ON psl.section_id = ps.section_id
    WHERE  ps.pattern_id = %s
    ORDER  BY ps.section_order, psl.parent_slot_id NULLS FIRST, psl.slot_order
"""

_SQL_FIND_QUESTION_EXACT = """
    SELECT question_id, content, marks_max
    FROM   questions
    WHERE  status = 'live'
      AND  style = %s
      AND  marks_max = %s
      AND  question_id NOT IN (
               SELECT pq.question_id
               FROM   paper_questions pq
               JOIN   paper_sections  psec ON psec.paper_section_id = pq.paper_section_id
               WHERE  psec.paper_id = %s
           )
    ORDER BY random()
    LIMIT 1
"""

_SQL_FIND_QUESTION_FUZZY = """
    SELECT question_id, content, marks_max
    FROM   questions
    WHERE  status = 'live'
      AND  style = %s
      AND  marks_max BETWEEN %s AND %s
      AND  question_id NOT IN (
               SELECT pq.question_id
               FROM   paper_questions pq
               JOIN   paper_sections  psec ON psec.paper_section_id = pq.paper_section_id
               WHERE  psec.paper_id = %s
           )
    ORDER BY ABS(marks_max - %s), random()
    LIMIT 1
"""


def _build_pattern_tree(cur, pattern_id: str) -> dict:
    """Fetch pattern metadata and tree structure. Returns a dict with
    sections, each containing a list of top-level slots with children."""

    cur.execute(_SQL_PATTERN_META, (pattern_id,))
    meta = cur.fetchone()
    if meta is None:
        raise ValueError(f"pattern_id {pattern_id} not found")

    name, total_marks, is_active = meta
    if not is_active:
        raise ValueError(
            f"pattern '{name}' (id={pattern_id}) is retired (is_active=False) "
            f"— cannot generate a paper from an inactive pattern"
        )

    cur.execute(_SQL_PATTERN_TREE, (pattern_id,))
    rows = cur.fetchall()

    if not rows:
        raise ValueError(f"pattern {pattern_id} has no sections/slots")

    sections = {}
    slots_by_id = {}

    for (section_id, sec_order, sec_label, is_mandatory,
         slot_id, slot_label, slot_order, marks, style, parent_id) in rows:
        sec_key = str(section_id)
        sections.setdefault(sec_key, {
            "section_id": str(section_id),
            "section_order": sec_order,
            "section_label": sec_label,
            "is_mandatory": is_mandatory,
            "top_slots": [],
        })
        slot_rec = {
            "slot_id": str(slot_id),
            "slot_label": slot_label,
            "slot_order": slot_order,
            "marks": marks,
            "style": style,
            "children": [],
        }
        slots_by_id[str(slot_id)] = slot_rec
        if parent_id is None:
            sections[sec_key]["top_slots"].append(slot_rec)
        else:
            parent_key = str(parent_id)
            if parent_key in slots_by_id:
                slots_by_id[parent_key]["children"].append(slot_rec)

    # Sort sections by order
    sorted_sections = sorted(sections.values(), key=lambda s: s["section_order"])

    return {
        "pattern_name": name,
        "total_marks": total_marks,
        "sections": sorted_sections,
    }


def _find_question(cur, style: str, marks: float, paper_id: str,
                   marks_tolerance: float = 1.0) -> dict | None:
    """Find a matching live question. Returns {question_id, content, marks_max}
    or None if no match."""

    # Try exact match first
    cur.execute(_SQL_FIND_QUESTION_EXACT, (style, marks, paper_id))
    row = cur.fetchone()
    if row:
        return {"question_id": str(row[0]), "content": row[1], "marks_max": row[2]}

    # Fuzzy match: ± tolerance on marks
    if marks_tolerance > 0:
        cur.execute(_SQL_FIND_QUESTION_FUZZY, (
            style, marks - marks_tolerance, marks + marks_tolerance,
            paper_id, marks,
        ))
        row = cur.fetchone()
        if row:
            return {"question_id": str(row[0]), "content": row[1], "marks_max": row[2]}

    return None


def _collect_leaf_slots(slot: dict) -> list[dict]:
    """Recursively collect leaf slots (no children). Parent slots are
    structural and don't get assigned a question."""
    if slot["children"]:
        leaves = []
        for child in slot["children"]:
            leaves.extend(_collect_leaf_slots(child))
        return leaves
    return [slot]


def generate_paper(cur, pattern_id: str, name: str,
                   generated_by: str | None = None,
                   choose_count: int = 1,
                   marks_tolerance: float = 1.0) -> dict:
    """Generate a paper from a pattern. Caller owns the transaction.

    Args:
        cur: open DB cursor
        pattern_id: UUID of the paper_pattern to fill
        name: human-readable paper name
        generated_by: reviewer UUID (optional)
        choose_count: default choose_count for optional sections
        marks_tolerance: allowed ± on marks_max when no exact match

    Returns a dict with the full paper structure including assigned questions
    and any warnings about unfilled slots.
    """
    tree = _build_pattern_tree(cur, pattern_id)

    # Validate generated_by if provided
    if generated_by is not None:
        cur.execute("SELECT reviewer_id FROM reviewers WHERE reviewer_id = %s",
                    (generated_by,))
        if cur.fetchone() is None:
            raise ValueError(f"generated_by reviewer_id {generated_by} not found")

    # Create the paper record
    paper_id = str(uuid.uuid4())
    cur.execute("""
        INSERT INTO generated_papers
            (paper_id, pattern_id, name, status, generated_at, generated_by)
        VALUES (%s, %s, %s, 'draft', now(), %s)
    """, (paper_id, pattern_id, name, generated_by))

    result_sections = []
    total_filled = 0
    total_slots = 0
    filled_marks = 0.0
    warnings = []

    for section in tree["sections"]:
        # Compute choose_count for this section
        top_slot_count = len(section["top_slots"])
        if section["is_mandatory"]:
            sec_choose_count = top_slot_count
        else:
            sec_choose_count = min(choose_count, top_slot_count)

        # Create paper_section
        paper_section_id = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO paper_sections
                (paper_section_id, paper_id, section_id, choose_count)
            VALUES (%s, %s, %s, %s)
        """, (paper_section_id, paper_id, section["section_id"], sec_choose_count))

        result_slots = []

        for top_slot in section["top_slots"]:
            # Collect leaf slots for this top-level slot
            leaves = _collect_leaf_slots(top_slot)

            slot_results = []
            for leaf in leaves:
                total_slots += 1
                match = _find_question(
                    cur, leaf["style"], leaf["marks"], paper_id,
                    marks_tolerance=marks_tolerance,
                )

                if match:
                    # Assign the question
                    paper_question_id = str(uuid.uuid4())
                    cur.execute("""
                        INSERT INTO paper_questions
                            (paper_question_id, paper_section_id, slot_id, question_id)
                        VALUES (%s, %s, %s, %s)
                    """, (paper_question_id, paper_section_id,
                          leaf["slot_id"], match["question_id"]))
                    total_filled += 1
                    # The SLOT's own marks, not the matched question's — a
                    # fuzzy match within marks_tolerance can differ slightly
                    # from the slot, and filled_marks reports what the PAPER
                    # actually accounts for against pattern_total_marks, not
                    # what the bank happened to have.
                    filled_marks += leaf["marks"]
                    slot_results.append({
                        "slot_id": leaf["slot_id"],
                        "slot_label": leaf["slot_label"],
                        "marks": leaf["marks"],
                        "style": leaf["style"],
                        "assigned": True,
                        "question_id": match["question_id"],
                        "question_content": match["content"],
                        "question_marks": match["marks_max"],
                    })
                else:
                    # No match — record a STRUCTURED warning (slot_label,
                    # section, marks, reason), not a prose string. is_mandatory
                    # travels with it so the check below can tell a hole in an
                    # OPTIONAL slot (a warning on the success response) from
                    # one in a MANDATORY slot (fatal — see below) without
                    # re-parsing a message for a magic prefix.
                    warnings.append({
                        "slot_label": leaf["slot_label"],
                        "section": section["section_label"],
                        "marks": leaf["marks"],
                        "reason": (
                            f"No live question matches style={leaf['style']!r}, "
                            f"marks={leaf['marks']} (±{marks_tolerance} "
                            f"tolerance)."
                        ),
                        "is_mandatory": section["is_mandatory"],
                    })
                    slot_results.append({
                        "slot_id": leaf["slot_id"],
                        "slot_label": leaf["slot_label"],
                        "marks": leaf["marks"],
                        "style": leaf["style"],
                        "assigned": False,
                        "question_id": None,
                        "question_content": None,
                        "question_marks": None,
                    })

            result_slots.append({
                "slot_label": top_slot["slot_label"],
                "marks": top_slot["marks"],
                "style": top_slot["style"],
                "is_parent": bool(top_slot["children"]),
                "leaves": slot_results,
            })

        result_sections.append({
            "section_label": section["section_label"],
            "section_order": section["section_order"],
            "is_mandatory": section["is_mandatory"],
            "choose_count": sec_choose_count,
            "slots": result_slots,
        })

    # A MANDATORY slot with no match means no valid paper exists for this
    # pattern against today's bank — fatal, and nothing above is committed
    # (api/routers/papers.py's docstring: the caller's transaction rolls back
    # on this exception, so the rows written above never become visible).
    mandatory_gaps = [w for w in warnings if w["is_mandatory"]]
    if mandatory_gaps:
        listed = "\n".join(
            f"  - {w['section']} / {w['slot_label']} ({w['marks']}M): {w['reason']}"
            for w in mandatory_gaps
        )
        raise ValueError(
            f"Cannot generate paper: {len(mandatory_gaps)} mandatory slot(s) "
            f"could not be filled:\n{listed}"
        )

    # Past this point every remaining warning is about an OPTIONAL slot —
    # `is_mandatory` has done its job distinguishing them and would be
    # redundant (always False) on every warning the caller actually sees.
    optional_gaps = [
        {k: v for k, v in w.items() if k != "is_mandatory"} for w in warnings
    ]

    return {
        "paper_id": paper_id,
        "pattern_id": pattern_id,
        "pattern_name": tree["pattern_name"],
        "name": name,
        "status": "draft",
        "total_filled": total_filled,
        "total_slots": total_slots,
        "is_complete": not optional_gaps,
        "filled_marks": filled_marks,
        "pattern_total_marks": tree["total_marks"],
        "sections": result_sections,
        "warnings": optional_gaps,
    }
