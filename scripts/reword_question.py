#!/usr/bin/env python3
"""
scripts/reword_question.py — Task 2c: reword an existing question and
create a new one from it, keeping a tree relationship between original
and derived questions.

BEHAVIOUR (updated per boss's instruction):
  The original question is NEVER superseded or retired by rewording.
  Both the original and the reworded version coexist in the question bank
  — teachers can use either. The relationship is tracked via
  parent_question_id (added in migration 005), forming a derivation tree:

      Q_original (parent_question_id = NULL)
         ├── Q_reword_1 (parent_question_id = Q_original)
         │       └── Q_reword_1a (parent_question_id = Q_reword_1)
         └── Q_reword_2 (parent_question_id = Q_original)

  This is distinct from supersedes_question_id, which means strict
  replacement (original retires) — used when correcting a factual error
  in a live question, not when creatively rewording it.

  The reworded question is inserted as status='draft' — it still goes
  through question_reviews before it can go live. Rewording never
  bypasses human review.

  Metadata copied forward to the new question:
    - question_keywords (same topics/difficulty still apply)
    - topic_links (entity_type='question')
    - question_asset_links (role='question_source' only)

  NOT copied forward:
    - reference_answer_variants — rewording the question doesn't change
      what a correct answer looks like.
    - answers rows — students who answered the original stay linked to it.

Written as a plain function so a future API endpoint can call it
directly without shelling out.

Usage:
    export LLM_PROVIDER=groq GROQ_API_KEY=...
    export PGHOST=... PGDATABASE=... PGUSER=... PGPASSWORD=...

    python3 scripts/reword_question.py <question_id>
    python3 scripts/reword_question.py <question_id> --instruction "make it harder"
    python3 scripts/reword_question.py <question_id> --dry-run
    python3 scripts/reword_question.py <question_id> --dry-run --stub-llm
"""
import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db as db_mod   # noqa: E402
from core import llm            # noqa: E402


def reword_question(cur, question_id: str, instruction: str | None = None,
                     stub_llm: bool = False) -> dict:
    """Runs the full reword operation against an open cursor. Caller owns
    the transaction — commit/rollback is the caller's responsibility."""

    cur.execute("""
        SELECT source_type, source_id, style, marks_max, status,
               question_group_id, content, parent_question_id
        FROM questions WHERE question_id = %s
    """, (question_id,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"question_id {question_id} not found")

    source_type, source_id, style, marks_max, status, \
        group_id, old_content, existing_parent = row

    if stub_llm:
        new_content = f"[REWORDED] {old_content}"
    else:
        new_content = llm.reword_question_text(
            old_content, instruction=instruction, style=style
        )

    new_question_id = str(uuid.uuid4())

    # Insert the derived question.
    # - parent_question_id links it back to the source question (tree edge)
    # - supersedes_question_id is NULL — original is NOT retired
    # - is_ai_generated = True since an LLM produced the text
    # - status = 'draft' — must go through question_reviews before going live
    cur.execute("""
        INSERT INTO questions (
            question_id, source_type, source_id, style, marks_max,
            status, supersedes_question_id, parent_question_id,
            question_group_id, created_at, content, is_ai_generated
        )
        VALUES (%s, %s, %s, %s, %s, 'draft', NULL, %s, %s, now(), %s, %s)
    """, (
        new_question_id, source_type, source_id, style, marks_max,
        question_id,    # parent_question_id = the question we rewrote
        group_id,       # same group_id keeps all versions aggregatable
        new_content,
        not stub_llm,   # is_ai_generated = True for real LLM calls, False for stubs
    ))

    # Original question: status unchanged, NOT superseded.
    # No question_status_history entry needed — nothing changed on it.

    # Copy metadata forward — the derived question covers the same
    # topics/keywords/source material as the original.
    cur.execute("""
        INSERT INTO question_keywords (question_id, keyword_id, weight)
        SELECT %s, keyword_id, weight FROM question_keywords
        WHERE question_id = %s
        ON CONFLICT (question_id, keyword_id) DO NOTHING
    """, (new_question_id, question_id))

    cur.execute("""
        INSERT INTO topic_links (link_id, entity_type, entity_id, topic_id)
        SELECT gen_random_uuid(), 'question', %s, topic_id
        FROM topic_links
        WHERE entity_type = 'question' AND entity_id = %s
    """, (new_question_id, question_id))

    cur.execute("""
        INSERT INTO question_asset_links
            (link_id, asset_id, role, question_id, reference_answer_variant_id)
        SELECT gen_random_uuid(), asset_id, 'question_source', %s, NULL
        FROM question_asset_links
        WHERE role = 'question_source' AND question_id = %s
    """, (new_question_id, question_id))

    return {
        "original_question_id": question_id,
        "original_status": status,          # unchanged
        "original_superseded": False,       # never superseded by rewording
        "new_question_id": new_question_id,
        "new_status": "draft",
        "parent_question_id": question_id,  # tree link
        "question_group_id": group_id,
        "original_content": old_content,
        "new_content": new_content,
    }


def show_tree(cur, root_id: str):
    """Print the derivation tree rooted at root_id using the question_tree
    view added in migration 005."""
    cur.execute("""
        SELECT depth, question_id, status, is_ai_generated,
               left(content, 60) AS snippet
        FROM question_tree
        WHERE root_question_id = %s
        ORDER BY path
    """, (root_id,))
    rows = cur.fetchall()
    print(f"\nDerivation tree rooted at {root_id}:")
    for depth, qid, status, is_ai, snippet in rows:
        indent = "  " * depth
        tag = "[AI]" if is_ai else "[human]"
        print(f"{indent}{'└─ ' if depth > 0 else ''}{tag} {qid} ({status}): {snippet!r}...")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("question_id")
    ap.add_argument("--instruction", default=None,
                    help="e.g. 'make it harder', 'simplify for beginners'")
    ap.add_argument("--show-tree", action="store_true",
                    help="after rewording, print the full derivation tree")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the result, roll back instead of committing")
    ap.add_argument("--stub-llm", action="store_true",
                    help="skip the real LLM call (for testing without an API key)")
    args = ap.parse_args()

    try:
        with db_mod.transaction(dry_run=args.dry_run) as cur:
            result = reword_question(
                cur, args.question_id,
                instruction=args.instruction,
                stub_llm=args.stub_llm,
            )

            print(f"Original ({result['original_question_id']}, "
                  f"status={result['original_status']}, NOT superseded):")
            print(f"  {result['original_content']!r}")
            print(f"New draft ({result['new_question_id']}, "
                  f"parent={result['parent_question_id']}):")
            print(f"  {result['new_content']!r}")

            if args.show_tree:
                show_tree(cur, args.question_id)

        if args.dry_run:
            print("\n[dry-run] rolled back, no changes persisted.")
        else:
            print("\nCommitted.")
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
