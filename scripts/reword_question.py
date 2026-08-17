#!/usr/bin/env python3
"""
scripts/reword_question.py — Task 2c: "how can you re word an existing
question."

Given a question_id, generates a reworded version of its text and inserts
it as a NEW `questions` row using the versioning mechanism the schema
already has — no new tables needed:

  1. INSERT a new row: status='draft', supersedes_question_id = the
     original's question_id, same question_group_id, new `content`.
     It goes through the normal question_reviews flow before it can go
     live — rewording never bypasses human review.
  2. The original row's status flips to 'superseded' (only if it was
     currently draft/confirmed/live — a question that's already
     rejected/superseded is left alone, since overwriting that historical
     status would misrepresent why it stopped being active), logged in
     question_status_history.
  3. question_keywords, topic_links (entity_type='question'), and
     question_asset_links (role='question_source') are copied forward to
     the new question_id — a reworded question is still about the same
     topics/keywords/source diagram, only the wording changed.

Deliberately NOT touched: reference_answer_variants and
question_asset_links with role='answer_component'. Rewording the
QUESTION doesn't change what counts as a correct ANSWER. Also not
touched: any `answers` rows already submitted against the original
question_id — they stay linked to the exact version a student actually
answered, per the project's append-only/audit-trail principle.

Written as a plain function (`reword_question`) so a future API endpoint
can call it directly without shelling out — the CLI below is a thin
wrapper for standalone testing.

Usage:
    export GROQ_API_KEY=...
    export PGHOST=... PGDATABASE=... PGUSER=... PGPASSWORD=...
    python3 scripts/reword_question.py <question_id> --instruction "make it harder"
    python3 scripts/reword_question.py <question_id> --dry-run              # no DB writes
    python3 scripts/reword_question.py <question_id> --dry-run --stub-llm   # no DB writes, no API call either
"""
import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db as db_mod   # noqa: E402
from core import llm            # noqa: E402

ACTIVE_STATUSES = ("draft", "confirmed", "live")


def reword_question(cur, question_id: str, instruction: str | None = None,
                     changed_by: str | None = None, stub_llm: bool = False) -> dict:
    """Runs the full reword operation against an open cursor (caller owns
    the transaction — commit/rollback is the caller's responsibility, same
    convention as load_exam_bank.py). Returns a summary dict."""

    cur.execute("""
        SELECT source_type, source_id, style, marks_max, status, question_group_id, content
        FROM questions WHERE question_id = %s
    """, (question_id,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"question_id {question_id} not found")
    source_type, source_id, style, marks_max, old_status, group_id, old_content = row

    if stub_llm:
        new_content = f"[REWORDED] {old_content}"
    else:
        new_content = llm.reword_question_text(old_content, instruction=instruction, style=style)

    new_question_id = str(uuid.uuid4())

    cur.execute("""
        INSERT INTO questions (question_id, source_type, source_id, style, marks_max,
                                status, supersedes_question_id, question_group_id, created_at, content)
        VALUES (%s, %s, %s, %s, %s, 'draft', %s, %s, now(), %s)
    """, (new_question_id, source_type, source_id, style, marks_max, question_id, group_id, new_content))

    superseded = False
    if old_status in ACTIVE_STATUSES:
        cur.execute("UPDATE questions SET status = 'superseded' WHERE question_id = %s", (question_id,))
        cur.execute("""
            INSERT INTO question_status_history (history_id, question_id, old_status, new_status, changed_by, changed_at)
            VALUES (%s, %s, %s, 'superseded', %s, now())
        """, (str(uuid.uuid4()), question_id, old_status, changed_by))
        superseded = True

    cur.execute("""
        INSERT INTO question_keywords (question_id, keyword_id, weight)
        SELECT %s, keyword_id, weight FROM question_keywords WHERE question_id = %s
        ON CONFLICT (question_id, keyword_id) DO NOTHING
    """, (new_question_id, question_id))

    cur.execute("""
        INSERT INTO topic_links (link_id, entity_type, entity_id, topic_id)
        SELECT gen_random_uuid(), 'question', %s, topic_id
        FROM topic_links WHERE entity_type = 'question' AND entity_id = %s
    """, (new_question_id, question_id))

    cur.execute("""
        INSERT INTO question_asset_links (link_id, asset_id, role, question_id, reference_answer_variant_id)
        SELECT gen_random_uuid(), asset_id, 'question_source', %s, NULL
        FROM question_asset_links WHERE role = 'question_source' AND question_id = %s
    """, (new_question_id, question_id))

    return {
        "original_question_id": question_id,
        "original_status_before": old_status,
        "original_superseded": superseded,
        "new_question_id": new_question_id,
        "new_status": "draft",
        "question_group_id": group_id,
        "original_content": old_content,
        "new_content": new_content,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question_id")
    ap.add_argument("--instruction", default=None, help="e.g. 'make it harder', 'simplify for beginners'")
    ap.add_argument("--changed-by", default=None, help="reviewer_id to attribute the supersede to (optional)")
    ap.add_argument("--dry-run", action="store_true", help="print the result, roll back instead of committing")
    ap.add_argument("--stub-llm", action="store_true", help="skip the real LLM call (for testing without an API key)")
    args = ap.parse_args()

    conn = db_mod.get_connection()
    try:
        with conn.cursor() as cur:
            result = reword_question(cur, args.question_id, instruction=args.instruction,
                                      changed_by=args.changed_by, stub_llm=args.stub_llm)
            print(f"Original ({result['original_question_id']}, was {result['original_status_before']}):")
            print(f"  {result['original_content']!r}")
            print(f"New draft ({result['new_question_id']}):")
            print(f"  {result['new_content']!r}")
            print(f"Original superseded: {result['original_superseded']}")

            if args.dry_run:
                conn.rollback()
                print("[dry-run] rolled back, no changes persisted.")
            else:
                conn.commit()
                print("Committed.")
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
