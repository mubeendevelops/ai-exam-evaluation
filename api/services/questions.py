"""api/services/questions.py — adapter between the question routers and the
existing generation / two-gate review logic.

TRANSLATION ONLY, and here that rule has teeth. The two-gate flow
(draft -> confirmed/rejected -> live) is implemented ONCE, in
scripts/review_question.py, whose functions were written as plain
cursor-taking functions precisely so an API could call them without shelling
out (that file's docstring says so). This module calls them. It does not
re-check statuses, does not re-order the gates, and above all does not offer
a path around them.

╔══════════════════════════════════════════════════════════════════════════╗
║ THE ONE INVARIANT: THERE IS NO WAY TO REACH 'live' WITHOUT TWO HUMAN     ║
║ DECISIONS, AND NOTHING IN api/ MAY ADD ONE.                             ║
╚══════════════════════════════════════════════════════════════════════════╝

Concretely, and each of these is a thing a future "convenience" would undo:

  * `generate` writes status='draft', hardcoded inside
    scripts/generate_questions.py's INSERT. This module exposes no status
    argument, so no request body can influence where a new question starts.
  * `review` fires only from 'draft'; `promote` fires only from 'confirmed'.
    Those checks live in review_question.py and run on every call made here.
  * There is deliberately NO combined "confirm and publish" helper. It would
    be two lines and it would collapse the publish gate into the quality one,
    which is the entire distinction the two gates exist to draw.

ERROR TRANSLATION, AND WHY IT RE-QUERIES

review_question.py raises a bare ValueError for all three of "no such
question", "no such reviewer" and "wrong status for this gate". Those are a
404, a 400 and a 409 respectively, and the router needs to tell them apart.
Matching on the exception's message text would work today and break the first
time someone rewords a string.

So `_classify` asks the database instead. It runs only on the failure path,
after the operation has ALREADY been refused, and it decides nothing about
whether the operation may proceed — the authority for that stays entirely in
review_question.py. If _classify were wrong about a status, the call would
still have failed, and the only consequence would be a mislabelled HTTP code.
That asymmetry is the point: the safeguard is upstream of the classifier and
cannot be weakened by it.

Re-querying is safe at this moment because those ValueErrors are raised
BEFORE any write in both functions (validation first, then INSERT/UPDATE), so
no partial row exists and the transaction is not in an error state — a Python
exception is not a failed statement.
"""
from __future__ import annotations

from typing import Any

import core.question_bank
from scripts.generate_questions import generate_questions as _generate_questions
from scripts.review_question import (
    promote_question as _promote_question,
    review_question as _review_question,
    show_question as _show_question,
)


class QuestionNotFoundError(LookupError):
    """No question with that id exists.

    Note what this does NOT mean, unlike its counterpart in
    api/services/evaluation.py: it is never "belongs to another college".
    The question bank is shared platform-wide (migration 003 lines 5, 341),
    so this genuinely means the id does not exist anywhere.
    """


class ReviewerNotFoundError(LookupError):
    """No reviewer with that id. A 400: the caller sent a bad reviewer_id.

    Not a 404 — the missing thing is a field in the request body, not the
    resource the URL names.
    """


class GateViolationError(RuntimeError):
    """The question is not in the status this gate transitions FROM.

    This is the class raised when someone tries to promote a draft, i.e. to
    skip human review. It carries the current and required statuses so the
    router can say exactly which gate was skipped, and it is a 409 Conflict:
    the request is well-formed and the caller may be entitled to do it — just
    not yet, and not from here.
    """

    def __init__(self, message: str, *, current_status: str, required_status: str):
        super().__init__(message)
        self.current_status = current_status
        self.required_status = required_status


def _classify(cur, question_id, reviewer_id, required_status: str,
              original: ValueError) -> Exception:
    """Turns a refusal from review_question.py into a typed error.

    Checks in the same order the underlying function does — question, then
    reviewer, then status — so the error a caller gets names the first thing
    that was actually wrong rather than an arbitrary one of several.
    """
    cur.execute("SELECT status FROM questions WHERE question_id = %s", (str(question_id),))
    row = cur.fetchone()
    if row is None:
        return QuestionNotFoundError(f"No question {question_id}.")
    current_status = row[0]

    cur.execute("SELECT 1 FROM reviewers WHERE reviewer_id = %s", (str(reviewer_id),))
    if cur.fetchone() is None:
        return ReviewerNotFoundError(
            f"No reviewer {reviewer_id}. Every status change is attributed, so "
            f"an unknown reviewer_id is refused rather than recorded."
        )

    if current_status != required_status:
        return GateViolationError(
            str(original), current_status=current_status, required_status=required_status
        )

    # Neither the question, the reviewer, nor the status explains it — the
    # operation was refused for a reason this function does not model (an
    # invalid `action`, say). Hand back the original rather than inventing a
    # classification, so the router answers 500 and the message survives.
    return original


# ───────────────────────────── generation ───────────────────────────────────

def generate(cur, *, paragraph_id, count: int = 3, style: str | None = None,
             intent_hint: str | None = None, stub_llm: bool = False) -> list[dict[str, Any]]:
    """Generates `count` DRAFT questions from one paragraph.

    A thin pass-through to scripts/generate_questions.py: the LLM prompting,
    the topic_links carry-forward and the fresh question_group_id per question
    are all decided there, where the CLI reaches them too.

    Raises ParagraphNotUsableError for a missing or superseded paragraph —
    both of which generate_questions() reports as a ValueError, and which are
    distinguished here for the same reason and by the same means as
    `_classify` above.
    """
    try:
        return _generate_questions(
            cur, str(paragraph_id), count=count, style=style,
            intent_hint=intent_hint, stub_llm=stub_llm,
        )
    except ValueError as exc:
        cur.execute("SELECT status FROM paragraphs WHERE paragraph_id = %s",
                    (str(paragraph_id),))
        row = cur.fetchone()
        if row is None:
            raise ParagraphNotFoundError(f"No paragraph {paragraph_id}.") from exc
        if row[0] != "active":
            raise ParagraphNotUsableError(
                f"Paragraph {paragraph_id} has status={row[0]!r}, not 'active'. "
                f"Generating questions from superseded content is refused.",
            ) from exc
        raise


class ParagraphNotFoundError(LookupError):
    """No paragraph with that id — a 404."""


class ParagraphNotUsableError(RuntimeError):
    """The paragraph exists but is superseded — a 409, same shape of answer as
    a gate violation: the resource is real, its current state forbids this."""


# ──────────────────────────────── reads ─────────────────────────────────────

def list_bank(cur, *, status=None, style=None, source_type=None,
              is_ai_generated=None, paper_id=None,
              limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    """A page of the bank plus the matching total. Straight through to
    core/question_bank.py, which owns the SQL."""
    rows = core.question_bank.list_questions(
        cur, status=status, style=style, source_type=source_type,
        is_ai_generated=is_ai_generated,
        paper_id=str(paper_id) if paper_id else None,
        limit=limit, offset=offset,
    )
    total = core.question_bank.count_questions(
        cur, status=status, style=style, source_type=source_type,
        is_ai_generated=is_ai_generated,
        paper_id=str(paper_id) if paper_id else None,
    )
    return rows, total


def get_one(cur, *, question_id) -> dict[str, Any]:
    """One question with its source paragraph and review history."""
    try:
        return _show_question(cur, str(question_id))
    except ValueError as exc:
        raise QuestionNotFoundError(f"No question {question_id}.") from exc


# ──────────────────────────── the two gates ─────────────────────────────────

def review(cur, *, question_id, reviewer_id, action: str,
           comment: str | None = None) -> dict[str, Any]:
    """GATE 1 (quality): draft -> confirmed | rejected.

    `action` is the CLI's verb ('confirm'/'reject'); review_question.py maps
    it to the resulting status and to the question_reviews action. That
    mapping is not duplicated here — if a third action is ever added, adding
    it there is what makes it exist, in both front doors at once.
    """
    try:
        return _review_question(cur, str(question_id), str(reviewer_id), action,
                                comment=comment)
    except ValueError as exc:
        raise _classify(cur, question_id, reviewer_id, "draft", exc) from exc


def promote(cur, *, question_id, reviewer_id) -> dict[str, Any]:
    """GATE 2 (publish): confirmed -> live.

    The 'confirmed' required-status passed to _classify is not a second copy
    of the rule — it is how the error is LABELLED after review_question.py has
    already refused. Promoting a draft raises GateViolationError here with
    current='draft', required='confirmed', which is the "cannot skip a gate"
    behaviour the test suite proves.
    """
    try:
        return _promote_question(cur, str(question_id), str(reviewer_id))
    except ValueError as exc:
        raise _classify(cur, question_id, reviewer_id, "confirmed", exc) from exc
