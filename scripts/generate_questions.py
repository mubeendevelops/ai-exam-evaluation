#!/usr/bin/env python3
"""
scripts/generate_questions.py — Task 1: generate exam questions from
uploaded content.

BEHAVIOUR (shared plumbing, approach-agnostic per boss's instruction):
  Flow: paragraphs (already uploaded/loaded) -> LLM generates question(s)
  -> land in `questions` as status='draft' -> must go through
  question_reviews before going live, same mandatory-review gate every
  other question-creation path in this codebase uses (see
  reword_question.py). Generation never bypasses human review.

  This script is deliberately agnostic to WHICH of the three generation
  approaches under evaluation (internet search / pgvector-RAG on the
  paragraphs corpus / teacher intent-hinted) produced the question text.
  All three approaches, however implemented, end up calling
  core.llm.generate_questions_from_content() and returning the same
  {content, style, marks_max} shape — this script only knows how to take
  that shape and turn it into rows. Swapping in RAG or web-search context
  later means changing core/llm.py, not this script.

  Each generated question is a NEW question, not a version of an existing
  one, so it gets its own fresh question_group_id (unlike
  reword_question.py, which reuses the original's group_id because
  rewording produces a version of the same question).

  parent_question_id is left NULL — that column tracks question-to-question
  derivation (rewording/variants, see migration 005), not paragraph-to-
  question provenance. Provenance is already captured by
  source_type='paragraph' + source_id=<paragraph_id>.

  Metadata copied forward from the source paragraph to each new question:
    - topic_links (entity_type='paragraph' -> entity_type='question'),
      same pattern reword_question.py uses to carry topic tags forward.

  NOT done here (intentionally out of scope for the plumbing-only cut):
    - keyword extraction/tagging of the generated question
    - reference_answer_variants (nothing yet asserts what a "correct"
      answer to an AI-generated question looks like; that's a separate,
      SME-owned step, not implied by generation)

Written as a plain function so a future API endpoint can call it directly
without shelling out (same convention as reword_question.py).

Usage:
    export LLM_PROVIDER=groq GROQ_API_KEY=...
    export PGHOST=... PGDATABASE=... PGUSER=... PGPASSWORD=...

    python3 scripts/generate_questions.py <paragraph_id>
    python3 scripts/generate_questions.py <paragraph_id> --count 5
    python3 scripts/generate_questions.py <paragraph_id> --style short
    python3 scripts/generate_questions.py <paragraph_id> --intent-hint "focus on definitions, easy difficulty"
    python3 scripts/generate_questions.py <paragraph_id> --dry-run --stub-llm
"""
import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db as db_mod   # noqa: E402
from core import llm            # noqa: E402


def _stub_questions(paragraph_content: str, count: int, style: str | None) -> list[dict]:
    """Deterministic fake output for --stub-llm, mirroring the
    [REWORDED] convention in reword_question.py's --stub-llm path."""
    snippet = paragraph_content.strip()[:40]
    return [
        {
            "content": f"[STUB Q{i+1}] What does the following describe: {snippet!r}?",
            "style": style or "short",
            "marks_max": 5.0,
        }
        for i in range(count)
    ]


def generate_questions(cur, paragraph_id: str, count: int = 3,
                        style: str | None = None, intent_hint: str | None = None,
                        stub_llm: bool = False) -> list[dict]:
    """Runs the full generation operation against an open cursor. Caller
    owns the transaction — commit/rollback is the caller's responsibility."""

    cur.execute("""
        SELECT content, status FROM paragraphs WHERE paragraph_id = %s
    """, (paragraph_id,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"paragraph_id {paragraph_id} not found")

    paragraph_content, paragraph_status = row
    if paragraph_status != "active":
        raise ValueError(
            f"paragraph {paragraph_id} has status={paragraph_status!r}, "
            f"expected 'active' — refusing to generate from superseded content"
        )

    if stub_llm:
        generated = _stub_questions(paragraph_content, count, style)
    else:
        generated = llm.generate_questions_from_content(
            paragraph_content, count=count, style=style, intent_hint=intent_hint,
        )

    created = []
    for item in generated:
        question_id = str(uuid.uuid4())
        group_id = str(uuid.uuid4())  # fresh group — this is a new question, not a version

        cur.execute("""
            INSERT INTO questions (
                question_id, source_type, source_id, style, marks_max,
                status, supersedes_question_id, parent_question_id,
                question_group_id, created_at, content, is_ai_generated
            )
            VALUES (%s, 'paragraph', %s, %s, %s, 'draft', NULL, NULL, %s, now(), %s, %s)
        """, (
            question_id, paragraph_id, item["style"], item["marks_max"],
            group_id, item["content"], not stub_llm,
        ))

        # Carry topic tags forward from the source paragraph — the
        # generated question covers the same topic(s) as its source.
        cur.execute("""
            INSERT INTO topic_links (link_id, entity_type, entity_id, topic_id)
            SELECT gen_random_uuid(), 'question', %s, topic_id
            FROM topic_links
            WHERE entity_type = 'paragraph' AND entity_id = %s
        """, (question_id, paragraph_id))

        created.append({
            "question_id": question_id,
            "question_group_id": group_id,
            "status": "draft",
            "style": item["style"],
            "marks_max": item["marks_max"],
            "content": item["content"],
        })

    return created


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("paragraph_id")
    ap.add_argument("--count", type=int, default=3,
                    help="how many questions to generate (default: 3)")
    ap.add_argument("--style", default=None, choices=list(llm.VALID_QUESTION_STYLES),
                    help="constrain every generated question to this style "
                         "(default: let the LLM choose per question)")
    ap.add_argument("--intent-hint", default=None,
                    help='teacher guidance, e.g. "focus on definitions, easy difficulty" '
                         "(the intent-hinted approach under evaluation for Task 1)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the result, roll back instead of committing")
    ap.add_argument("--stub-llm", action="store_true",
                    help="skip the real LLM call (for testing without an API key)")
    args = ap.parse_args()

    try:
        with db_mod.transaction(dry_run=args.dry_run) as cur:
            results = generate_questions(
                cur, args.paragraph_id,
                count=args.count, style=args.style, intent_hint=args.intent_hint,
                stub_llm=args.stub_llm,
            )

        print(f"Generated {len(results)} draft question(s) from paragraph "
              f"{args.paragraph_id}:\n")
        for r in results:
            print(f"  [{r['question_id']}] ({r['style']}, {r['marks_max']} marks, "
                  f"status={r['status']})")
            print(f"    {r['content']!r}")

        if args.dry_run:
            print("\n[dry-run] rolled back, no changes persisted.")
        else:
            print("\nCommitted. All questions are status='draft' — "
                  "pending review before going live.")
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
