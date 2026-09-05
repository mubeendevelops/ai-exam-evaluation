"""api/services/papers.py — adapter between the papers router and
core/paper_generator.py.

TRANSLATION ONLY. Every decision about how a paper gets filled — exact match
on style+marks, then a ±tolerance fallback, no repeats within a paper,
mandatory gaps are fatal while optional ones are warnings, parent slots are
structural — is made in core/paper_generator.py, where scripts/generate_paper.py
reaches it too. Nothing here changes which question lands in which slot.

WHAT `live` MEANS HERE, AND WHY THE ENDPOINT NEEDS NO FILTER OF ITS OWN

core/paper_generator.py's two matching queries both open with
`WHERE status = 'live'`. That is the payoff of the two-gate flow in
api/services/questions.py: a question can only be assigned to a paper after a
human confirmed it and a second decision published it. There is no code path
in this module that selects a question, so there is no code path here that
could accidentally admit a draft one — the constraint lives in the SQL, one
level down, for both front doors.

NO TENANT FILTER, DELIBERATELY. Papers, patterns and the question bank are
all shared across colleges (migrations 006 line 15, 008 line 15, 003 line 5):
none of these tables has a college_id and none is under RLS. The router still
takes a tenant-scoped connection — that is how the caller is authenticated and
it keeps every endpoint on one connection discipline — but it buys isolation
on the answer schema, not here. Adding a college predicate to these queries
would require a migration first, not a WHERE clause.
"""
from __future__ import annotations

from typing import Any

import core.paper_generator


class PatternNotFoundError(LookupError):
    """No paper_pattern with that id — a 404."""


class PatternNotUsableError(RuntimeError):
    """The pattern exists but cannot be generated from: it is retired
    (is_active false) or has no sections/slots. A 409 — the resource is real,
    its state forbids this."""


class ReviewerNotFoundError(LookupError):
    """generated_by names a reviewer that does not exist — a 400, since the
    bad value is a field of the request body."""


class UnfillableSlotsError(RuntimeError):
    """A MANDATORY slot had no matching live question, so no valid paper
    exists for this pattern against today's bank.

    A 409 with the generator's own message, which lists each unfilled slot by
    label, style and marks. That list is the actionable part: it says exactly
    which questions somebody has to write (and review, and promote) before
    this pattern can produce a paper. Nothing is persisted — generate_paper
    raises before the caller commits, so the partially built paper rows roll
    back with the request's transaction.
    """


def generate(cur, *, pattern_id, name: str, generated_by=None,
             choose_count: int = 1, marks_tolerance: float = 1.0) -> dict[str, Any]:
    """Fills one pattern with live questions and persists the paper.

    The ValueError classification mirrors api/services/questions.py: the
    generator raises a bare ValueError for four distinct causes, so on the
    failure path — and only there, after the operation has already been
    refused — this asks the database which one it was. The generator remains
    the only thing that decides whether generation may proceed.
    """
    try:
        return core.paper_generator.generate_paper(
            cur, str(pattern_id), name,
            generated_by=str(generated_by) if generated_by else None,
            choose_count=choose_count,
            marks_tolerance=marks_tolerance,
        )
    except ValueError as exc:
        raise _classify(cur, pattern_id, generated_by, exc) from exc


def _classify(cur, pattern_id, generated_by, original: ValueError) -> Exception:
    """Same order of checks the generator makes: pattern, then reviewer, then
    fillability."""
    cur.execute(
        "SELECT is_active FROM paper_patterns WHERE pattern_id = %s", (str(pattern_id),)
    )
    row = cur.fetchone()
    if row is None:
        return PatternNotFoundError(f"No paper pattern {pattern_id}.")
    if not row[0]:
        return PatternNotUsableError(
            f"Paper pattern {pattern_id} is retired (is_active=false). Generating "
            f"from it is refused."
        )

    if generated_by is not None:
        cur.execute("SELECT 1 FROM reviewers WHERE reviewer_id = %s", (str(generated_by),))
        if cur.fetchone() is None:
            return ReviewerNotFoundError(
                f"No reviewer {generated_by} for generated_by."
            )

    cur.execute("""
        SELECT COUNT(*)
        FROM   pattern_sections ps
        JOIN   pattern_slots    psl ON psl.section_id = ps.section_id
        WHERE  ps.pattern_id = %s
    """, (str(pattern_id),))
    (slot_count,) = cur.fetchone()
    if slot_count == 0:
        return PatternNotUsableError(
            f"Paper pattern {pattern_id} has no sections or slots to fill."
        )

    # The pattern is real, active and populated, and the reviewer checks out —
    # so what failed is the fill itself. The generator's message enumerates the
    # unfilled mandatory slots; it is carried through verbatim because that
    # list is the whole value of the error.
    return UnfillableSlotsError(str(original))
