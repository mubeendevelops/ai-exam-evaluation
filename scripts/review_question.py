#!/usr/bin/env python3
"""
scripts/review_question.py — Task 1 follow-up: lets a reviewer (teacher/
SME/admin) read an AI-generated draft question and confirm or reject it,
i.e. "remove the draft tag" per the mandatory-review gate every
question-creation path in this codebase already relies on
(scripts/generate_questions.py, scripts/reword_question.py both insert
status='draft' and stop there — this is the missing other half).

BEHAVIOUR:
  Two-gate publish model:
    draft --[confirm]--> confirmed   (quality gate: reviewer approves content)
    draft --[reject]  --> rejected
    confirmed --[promote]--> live    (publish gate: coordinator makes it answerable)

  A review action (--review) only fires from 'draft'. Re-reviewing an
  already-decided question is not supported — that would need its own
  explicit "re-open" operation.

  A promote action (--promote) only fires from 'confirmed'. It records the
  transition in question_status_history (for audit) but does NOT write a
  question_reviews row — promotion is an operational publish decision, not
  a content review.

  Both actions require a valid --reviewer-id for audit traceability.

  Every review action (--review) is recorded in two places:
    - question_reviews:        who reviewed it, what they decided, any comment
    - question_status_history: the status transition

  question_reviews / question_status_history / questions are all
  shared/global Question-schema tables (migration 003 explicitly excludes
  them from RLS/tenancy) — any reviewer, college-scoped or platform-level,
  can act on the shared bank. No college_id handling needed here.

Written as plain functions so a future API/UI can call them directly
without shelling out (same convention as reword_question.py /
generate_questions.py).

Usage:
    export PGHOST=... PGDATABASE=... PGUSER=... PGPASSWORD=...

    # Browse the review queue (oldest drafts first):
    python3 scripts/review_question.py --list
    python3 scripts/review_question.py --list --limit 10

    # Read one question in full, with its source paragraph for context:
    python3 scripts/review_question.py --show <question_id>

    # Confirm or reject a draft:
    python3 scripts/review_question.py --review <question_id> --reviewer-id <uuid> --action confirm
    python3 scripts/review_question.py --review <question_id> --reviewer-id <uuid> --action reject --comment "factually wrong"
    python3 scripts/review_question.py --review <question_id> --reviewer-id <uuid> --action confirm --dry-run

    # Publish a confirmed question (confirmed -> live):
    python3 scripts/review_question.py --promote <question_id> --reviewer-id <uuid>
    python3 scripts/review_question.py --promote <question_id> --reviewer-id <uuid> --dry-run
"""
import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db as db_mod   # noqa: E402

ACTION_TO_STATUS = {"confirm": "confirmed", "reject": "rejected"}
ACTION_TO_REVIEW_ACTION = {"confirm": "confirmed", "reject": "rejected"}


def list_reviewers(cur) -> list[dict]:
    """Lookup helper — there is no reviewer-management UI/API yet, so this
    is how a caller finds a valid --reviewer-id without hand-writing SQL."""
    cur.execute("SELECT reviewer_id, name, role, email, college_id FROM reviewers ORDER BY name")
    rows = cur.fetchall()
    return [
        {"reviewer_id": r[0], "name": r[1], "role": r[2], "email": r[3], "college_id": r[4]}
        for r in rows
    ]


def list_draft_questions(cur, limit: int = 20) -> list[dict]:
    """Oldest-first review queue. Joins in the source paragraph's content
    (when source_type='paragraph') so the queue is scannable without a
    second lookup per row."""
    cur.execute("""
        SELECT q.question_id, q.content, q.style, q.marks_max,
               q.source_type, q.source_id, q.is_ai_generated, q.created_at,
               p.content AS source_paragraph_content
        FROM questions q
        LEFT JOIN paragraphs p
            ON q.source_type = 'paragraph' AND q.source_id = p.paragraph_id
        WHERE q.status = 'draft'
        ORDER BY q.created_at ASC
        LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    return [
        {
            "question_id": r[0], "content": r[1], "style": r[2], "marks_max": r[3],
            "source_type": r[4], "source_id": r[5], "is_ai_generated": r[6],
            "created_at": r[7], "source_paragraph_content": r[8],
        }
        for r in rows
    ]


def show_question(cur, question_id: str) -> dict:
    """Full detail for one question, including its source paragraph (if
    any) so a reviewer can check the question against what it was
    generated from, and its existing question_reviews history."""
    cur.execute("""
        SELECT q.question_id, q.content, q.style, q.marks_max, q.status,
               q.source_type, q.source_id, q.is_ai_generated,
               q.question_group_id, q.parent_question_id, q.created_at,
               p.content AS source_paragraph_content
        FROM questions q
        LEFT JOIN paragraphs p
            ON q.source_type = 'paragraph' AND q.source_id = p.paragraph_id
        WHERE q.question_id = %s
    """, (question_id,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"question_id {question_id} not found")

    cur.execute("""
        SELECT reviewer_id, action, comment, reviewed_at
        FROM question_reviews
        WHERE question_id = %s
        ORDER BY reviewed_at ASC
    """, (question_id,))
    reviews = cur.fetchall()

    return {
        "question_id": row[0], "content": row[1], "style": row[2],
        "marks_max": row[3], "status": row[4], "source_type": row[5],
        "source_id": row[6], "is_ai_generated": row[7],
        "question_group_id": row[8], "parent_question_id": row[9],
        "created_at": row[10], "source_paragraph_content": row[11],
        "prior_reviews": [
            {"reviewer_id": rr[0], "action": rr[1], "comment": rr[2], "reviewed_at": rr[3]}
            for rr in reviews
        ],
    }


def review_question(cur, question_id: str, reviewer_id: str, action: str,
                     comment: str | None = None) -> dict:
    """Runs the full review operation against an open cursor. Caller owns
    the transaction — commit/rollback is the caller's responsibility.

    Quality gate: draft -> confirmed (or rejected). Records both a
    question_reviews row and a question_status_history row.
    """
    if action not in ACTION_TO_STATUS:
        raise ValueError(f"action must be one of {list(ACTION_TO_STATUS)}, got {action!r}")

    cur.execute("SELECT status FROM questions WHERE question_id = %s", (question_id,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"question_id {question_id} not found")
    current_status = row[0]
    if current_status != "draft":
        raise ValueError(
            f"question {question_id} has status={current_status!r}, expected 'draft' — "
            f"it has already been reviewed (or was never a draft to begin with)"
        )

    cur.execute("SELECT reviewer_id FROM reviewers WHERE reviewer_id = %s", (reviewer_id,))
    if cur.fetchone() is None:
        raise ValueError(f"reviewer_id {reviewer_id} not found in reviewers")

    new_status = ACTION_TO_STATUS[action]
    review_action = ACTION_TO_REVIEW_ACTION[action]
    review_id = str(uuid.uuid4())
    history_id = str(uuid.uuid4())

    cur.execute("""
        INSERT INTO question_reviews (review_id, question_id, reviewer_id, action, comment, reviewed_at)
        VALUES (%s, %s, %s, %s, %s, now())
    """, (review_id, question_id, reviewer_id, review_action, comment))

    cur.execute("""
        UPDATE questions SET status = %s WHERE question_id = %s
    """, (new_status, question_id))

    cur.execute("""
        INSERT INTO question_status_history
            (history_id, question_id, old_status, new_status, changed_by, changed_at)
        VALUES (%s, %s, %s, %s, %s, now())
    """, (history_id, question_id, current_status, new_status, reviewer_id))

    return {
        "question_id": question_id,
        "old_status": current_status,
        "new_status": new_status,
        "review_id": review_id,
        "reviewer_id": reviewer_id,
        "comment": comment,
    }


def promote_question(cur, question_id: str, reviewer_id: str) -> dict:
    """Publish gate: confirmed -> live. Caller owns the transaction.

    Promotion is a separate operational decision from content review — it
    controls when a question becomes answerable by students (required by
    trg_answers_question_must_be_live). It is NOT recorded in
    question_reviews (that table tracks content quality decisions only);
    it IS recorded in question_status_history for a full audit trail.

    Requires a valid reviewer_id so every status change is attributable.
    """
    cur.execute("SELECT status FROM questions WHERE question_id = %s", (question_id,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"question_id {question_id} not found")
    current_status = row[0]
    if current_status != "confirmed":
        raise ValueError(
            f"question {question_id} has status={current_status!r}, expected 'confirmed' — "
            f"only confirmed questions can be promoted to live "
            f"(draft questions must be reviewed first)"
        )

    cur.execute("SELECT reviewer_id FROM reviewers WHERE reviewer_id = %s", (reviewer_id,))
    if cur.fetchone() is None:
        raise ValueError(f"reviewer_id {reviewer_id} not found in reviewers")

    history_id = str(uuid.uuid4())

    cur.execute("""
        UPDATE questions SET status = 'live' WHERE question_id = %s
    """, (question_id,))

    cur.execute("""
        INSERT INTO question_status_history
            (history_id, question_id, old_status, new_status, changed_by, changed_at)
        VALUES (%s, %s, %s, %s, %s, now())
    """, (history_id, question_id, current_status, "live", reviewer_id))

    return {
        "question_id": question_id,
        "old_status": current_status,
        "new_status": "live",
        "reviewer_id": reviewer_id,
    }


def _print_list(rows: list[dict]) -> None:
    if not rows:
        print("No draft questions pending review.")
        return
    for r in rows:
        print(f"[{r['question_id']}] ({r['style']}, {r['marks_max']} marks, "
              f"{'AI' if r['is_ai_generated'] else 'human'}, created {r['created_at']})")
        print(f"  {r['content']!r}")
    print(f"\n{len(rows)} draft question(s) shown.")


def _print_detail(d: dict) -> None:
    print(f"\n[{d['question_id']}] status={d['status']}")
    print(f"  style       : {d['style']}")
    print(f"  marks_max   : {d['marks_max']}")
    print(f"  source_type : {d['source_type']} ({d['source_id']})")
    print(f"  ai_generated: {d['is_ai_generated']}")
    print(f"\n  Question:\n    {d['content']!r}")
    if d["source_paragraph_content"]:
        print(f"\n  Source paragraph:\n    {d['source_paragraph_content']!r}")
    if d["prior_reviews"]:
        print("\n  Prior reviews:")
        for pr in d["prior_reviews"]:
            print(f"    {pr['reviewed_at']}  {pr['action']}  by {pr['reviewer_id']}"
                  + (f"  — {pr['comment']!r}" if pr["comment"] else ""))
    print()


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true",
                        help="list draft questions pending review, oldest first")
    group.add_argument("--show", metavar="QUESTION_ID",
                        help="show one question in full, with its source paragraph")
    group.add_argument("--review", metavar="QUESTION_ID",
                        help="confirm or reject one draft question "
                             "(requires --reviewer-id and --action)")
    group.add_argument("--promote", metavar="QUESTION_ID",
                        help="publish a confirmed question: confirmed -> live "
                             "(requires --reviewer-id; no --action needed)")
    group.add_argument("--list-reviewers", action="store_true",
                        help="list existing reviewers, to find a --reviewer-id to use "
                             "(no signup flow exists yet — see seed_example_reviewers.sql)")

    ap.add_argument("--limit", type=int, default=20, help="max rows for --list (default: 20)")
    ap.add_argument("--reviewer-id", default=None,
                     help="required with --review and --promote")
    ap.add_argument("--action", choices=["confirm", "reject"], default=None,
                     help="required with --review")
    ap.add_argument("--comment", default=None, help="optional reviewer comment (--review only)")
    ap.add_argument("--dry-run", action="store_true",
                     help="print the result, roll back instead of committing "
                          "(--review and --promote)")
    args = ap.parse_args()

    if args.review and (not args.reviewer_id or not args.action):
        ap.error("--review requires both --reviewer-id and --action")
    if args.promote and not args.reviewer_id:
        ap.error("--promote requires --reviewer-id")

    try:
        with db_mod.transaction(dry_run=args.dry_run) as cur:
            if args.list:
                _print_list(list_draft_questions(cur, limit=args.limit))
            elif args.list_reviewers:
                rows = list_reviewers(cur)
                if not rows:
                    print("No reviewers found — see scripts/seed_example_reviewers.sql.")
                else:
                    for r in rows:
                        scope = "global" if r["college_id"] is None else str(r["college_id"])[:8]
                        print(f"[{r['reviewer_id']}] {r['name']} ({r['role']}, {scope}) — {r['email']}")
            elif args.show:
                _print_detail(show_question(cur, args.show))
            elif args.review:
                result = review_question(
                    cur, args.review, args.reviewer_id, args.action, comment=args.comment
                )
                print(f"Question {result['question_id']}: "
                      f"{result['old_status']} -> {result['new_status']} "
                      f"(reviewer {result['reviewer_id']})")
                print("[dry-run] rolled back, no changes persisted." if args.dry_run else "Committed.")
            else:  # --promote
                result = promote_question(cur, args.promote, args.reviewer_id)
                print(f"Question {result['question_id']}: "
                      f"{result['old_status']} -> {result['new_status']} "
                      f"(promoted by reviewer {result['reviewer_id']})")
                print("[dry-run] rolled back, no changes persisted." if args.dry_run
                      else "Committed. Question is now live and answerable by students.")
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
