"""core/paragraphs.py — the content-upload pipeline's whole library: pure
text splitting, plus reads/writes over `paragraphs` and `sentences`.

WHY THIS EXISTS

Question generation (scripts/generate_questions.py) has always taken a
paragraph_id and refused to run without one, but nothing in this codebase —
no endpoint, no core function, no script, not even the seed data — ever
INSERTs into `paragraphs`. `sentences` has zero writers anywhere, including
tests. Every paragraph in a working deployment before this module existed had
to be inserted by hand (tests/test_api/conftest.py::make_paragraph is the one
place that happens, and its own docstring says so). This module is that
missing write path, plus the read path a picker UI needs — mirroring
core/question_bank.py, which CLAUDE_CONTEXT.md §11 names as "the
counter-example done right": the one query an endpoint needed that did not
already exist belongs in core/, not in api/services/, so a future CLI flag
finds it already written.

SPLITTING IS A SUGGESTION; SAVING IS A COMMITMENT. `split_into_paragraphs`
and `split_into_sentences` are pure functions with no cursor argument — the
API's POST /content/split calls them without opening a connection at all, so
a teacher can see how their pasted text would be cut up before anything is
written. `create_paragraphs` takes the (possibly hand-edited) final list, not
raw text, and is the only function here that writes.

TENANCY: there is none here, deliberately, for the same reason
core/question_bank.py has none. Migration 003 excludes the whole question
schema from RLS — "shared question bank, no tenant column, readable/usable
by every college" — and `paragraphs`/`sentences` are two of those twelve
tables. So no function below takes or filters on a college_id: adding one
would look like isolation while providing none, which is worse than the
honest absence. If uploaded content is ever tenanted, that is a migration
plus a change here, not a predicate bolted on at a call site.

CONNECTION CONTRACT: identical to core/question_bank.py and core/uploads.py.
The caller owns the cursor's transaction; nothing here commits.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

import core.pagination

#: paragraph_status enum (migration 002 line 22).
VALID_STATUSES = ("active", "superseded")

#: Re-exported for the same reason core/question_bank.py re-exports it: this
#: was not the first module to need a page-size ceiling, but every list_*()
#: here still enforces it independently (defense in depth — a caller that
#: reaches these functions without going through the API must not be able to
#: pull the whole corpus into memory either).
MAX_LIMIT = core.pagination.MAX_LIMIT

#: Blank-line-or-more is a paragraph break. Runs of two or more newlines
#: (after \r\n/\r are normalized to \n) so a single stray blank line inside
#: what is otherwise one paragraph does not fragment it.
_PARAGRAPH_BREAK_RE = re.compile(r"\n[ \t]*\n[ \t\n]*")

#: A simple terminator-based sentence splitter: split after ./!/? followed by
#: whitespace, provided that whitespace is followed by an uppercase letter,
#: a digit, or a quote/paren — not by a lowercase letter, which is what
#: follows a mid-sentence abbreviation like "e.g." or "Dr. Smith" far more
#: often than it follows a real sentence boundary. This is a heuristic, not a
#: parser: it will still split "U.S. policy" (splits after "U.S." only if
#: followed by an uppercase word, which "policy" is not, so that case is
#: actually fine) but will mis-split a sentence that happens to end with an
#: abbreviation immediately before a capitalized proper noun. Good enough for
#: `is_question_worthy` bookkeeping, which nothing downstream currently reads
#: as ground truth; not good enough to be treated as an NLP sentence
#: boundary detector.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def split_into_paragraphs(text: str) -> list[str]:
    """Splits raw pasted/uploaded text into candidate paragraphs on blank
    lines. Pure — no cursor, no DB, nothing persisted.

    Normalizes \\r\\n and bare \\r to \\n first (a Windows-authored .txt or a
    textarea's value can carry either), then splits on one-or-more blank
    lines, stripping and dropping any candidate that is empty after
    stripping (leading/trailing blank lines, or three-plus consecutive blank
    lines, must not produce an empty paragraph in the middle of the list).
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    candidates = _PARAGRAPH_BREAK_RE.split(normalized)
    return [c.strip() for c in candidates if c.strip()]


def split_into_sentences(paragraph: str) -> list[str]:
    """Splits one paragraph's text into sentences. Pure, same contract as
    `split_into_paragraphs`.

    Internal whitespace (including embedded newlines) is collapsed to single
    spaces before splitting, so a paragraph that itself contains a hard line
    break does not produce a spurious sentence boundary at that break.
    A paragraph with no terminator at all (a heading, a single clause) comes
    back as one sentence, not zero — this is "however many sentences", not
    "how many periods".
    """
    collapsed = re.sub(r"\s+", " ", paragraph.strip())
    if not collapsed:
        return []
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(collapsed) if s.strip()]


def _escape_like(term: str) -> str:
    """Escapes a caller-supplied search term for use inside an ILIKE
    pattern. Without this, a teacher searching for "50% off" or a stray "_"
    in their own content would have those characters interpreted as SQL
    wildcards rather than literal text."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class ParagraphNotFoundError(LookupError):
    """No paragraph with that id exists."""


class ParagraphAlreadySupersededError(RuntimeError):
    """The paragraph is already superseded.

    A 409, not a silent no-op: superseding is a one-way, reviewer-visible
    action (the same shape as the question bank's gate violations), and
    quietly succeeding a second time would hide from the caller that nothing
    changed just now — the paragraph was already retired by someone or
    something else.
    """


def create_paragraphs(
    cur, *, source_document: str, contents: list[str],
) -> list[dict[str, Any]]:
    """Inserts one `paragraphs` row per (already-edited) string in
    `contents`, plus its `sentences`, and returns them newest-last, in the
    order given.

    `contents` is the FINAL list a teacher has reviewed — typically the
    output of `split_into_paragraphs` after edits (merges, removals, manual
    additions) — not raw text; this function does no splitting of its own.
    Each is stripped and any left blank after stripping is dropped silently
    (the caller already saw and could have removed it in the review step);
    what remains must be non-empty, or there is nothing to create.

    `version` stays at its schema default of 1 and `status` is always
    'active' — a freshly uploaded paragraph has no prior version to be a
    revision of. Superseding is a status flip (`supersede_paragraph`), not a
    version bump.

    This is `sentences`' first writer in the codebase: one row per sentence,
    `sequence_order` 1..N, `is_question_worthy` left NULL — unknown, not
    False, exactly as the column's own type (nullable BOOLEAN) intends.
    Nothing downstream reads it today; it exists for a future
    sentence-level generation path to fill in.

    Does NOT commit — caller's transaction owns that, same contract as
    core/uploads.py::record_upload.
    """
    if not source_document or not source_document.strip():
        raise ValueError("source_document must not be blank")
    source_document = source_document.strip()

    cleaned = [c.strip() for c in contents if c and c.strip()]
    if not cleaned:
        raise ValueError(
            "contents has no non-blank paragraphs to create — nothing to save"
        )

    created: list[dict[str, Any]] = []
    for content in cleaned:
        cur.execute(
            """
            INSERT INTO paragraphs
                (paragraph_id, content, source_document, version, status)
            VALUES (gen_random_uuid(), %s, %s, 1, 'active')
            RETURNING paragraph_id, content, source_document, version, status,
                      uploaded_at
            """,
            (content, source_document),
        )
        row = cur.fetchone()
        paragraph_id = row[0]

        sentences = split_into_sentences(content)
        for order, sentence in enumerate(sentences, start=1):
            cur.execute(
                """
                INSERT INTO sentences
                    (sentence_id, paragraph_id, content, sequence_order,
                     is_question_worthy)
                VALUES (gen_random_uuid(), %s, %s, %s, NULL)
                """,
                (paragraph_id, sentence, order),
            )

        created.append({
            "paragraph_id": str(paragraph_id),
            "content": row[1],
            "source_document": row[2],
            "version": row[3],
            "status": row[4],
            "uploaded_at": row[5],
            "sentence_count": len(sentences),
            "question_count": 0,
        })

    return created


def _list_filters(status, source_document, search):
    where: list[str] = []
    params: list[Any] = []

    if status is not None:
        if status not in VALID_STATUSES:
            raise ValueError(f"status must be one of {VALID_STATUSES}, got {status!r}")
        where.append("p.status = %s")
        params.append(status)
    if source_document is not None:
        where.append("p.source_document = %s")
        params.append(source_document)
    if search is not None and search.strip():
        where.append("p.content ILIKE %s")
        params.append(f"%{_escape_like(search.strip())}%")

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    return where_sql, params


#: Correlated subqueries, not JOINs — a paragraph can have any number of
#: sentences or generated questions, and a JOIN would multiply each
#: paragraph row by that count before the LIMIT/OFFSET below ever sees it.
_SENTENCE_COUNT_SQL = "(SELECT COUNT(*) FROM sentences s WHERE s.paragraph_id = p.paragraph_id)"
_QUESTION_COUNT_SQL = (
    "(SELECT COUNT(*) FROM questions q "
    "WHERE q.source_type = 'paragraph' AND q.source_id = p.paragraph_id)"
)


def list_paragraphs(
    cur,
    *,
    status: str | None = None,
    source_document: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """A filtered page of uploaded content, newest first — the picker's list
    query.

    `search` is a case-insensitive substring match over `content`. `%`/`_`
    in the term are escaped (see `_escape_like`) so a teacher's own content
    containing either is not misread as a wildcard.

    Ordering is `uploaded_at DESC, paragraph_id DESC`, the same
    timestamp-plus-PK tiebreak core/question_bank.py uses: bulk-creating
    several paragraphs from one upload happens inside one transaction and can
    share a `now()` timestamp, and without the tiebreak LIMIT/OFFSET paging
    can show one row twice and skip another.
    """
    limit = core.pagination.clamp_limit(limit)
    if offset < 0:
        raise ValueError(f"offset must be >= 0, got {offset}")

    where_sql, params = _list_filters(status, source_document, search)

    cur.execute(f"""
        SELECT p.paragraph_id, p.content, p.source_document, p.version,
               p.status, p.uploaded_at,
               {_SENTENCE_COUNT_SQL} AS sentence_count,
               {_QUESTION_COUNT_SQL} AS question_count
        FROM   paragraphs p
        {where_sql}
        ORDER  BY p.uploaded_at DESC, p.paragraph_id DESC
        LIMIT  %s OFFSET %s
    """, (*params, limit, offset))

    return [
        {
            "paragraph_id": str(r[0]), "content": r[1], "source_document": r[2],
            "version": r[3], "status": r[4], "uploaded_at": r[5],
            "sentence_count": r[6], "question_count": r[7],
        }
        for r in cur.fetchall()
    ]


def count_paragraphs(
    cur, *, status: str | None = None, source_document: str | None = None,
    search: str | None = None,
) -> int:
    """Total matching `list_paragraphs`'s filters, ignoring limit/offset."""
    where_sql, params = _list_filters(status, source_document, search)
    cur.execute(f"SELECT COUNT(*) FROM paragraphs p {where_sql}", tuple(params))
    (total,) = cur.fetchone()
    return int(total)


def get_paragraph(cur, *, paragraph_id) -> dict[str, Any] | None:
    """One paragraph in full: its content, every sentence in order, and every
    question generated from it — the picker's detail rail. None if no such
    paragraph exists (never raises; unlike `supersede_paragraph`, there is no
    write here to refuse, so the caller just gets nothing to show).
    """
    cur.execute("""
        SELECT paragraph_id, content, source_document, version, status, uploaded_at
        FROM   paragraphs
        WHERE  paragraph_id = %s
    """, (str(paragraph_id),))
    row = cur.fetchone()
    if row is None:
        return None

    cur.execute("""
        SELECT content
        FROM   sentences
        WHERE  paragraph_id = %s
        ORDER  BY sequence_order
    """, (str(paragraph_id),))
    sentences = [s[0] for s in cur.fetchall()]

    cur.execute("""
        SELECT question_id, content, status, style, marks_max, source_type,
               source_id, is_ai_generated, question_group_id,
               parent_question_id, created_at
        FROM   questions
        WHERE  source_type = 'paragraph' AND source_id = %s
        ORDER  BY created_at DESC, question_id DESC
    """, (str(paragraph_id),))
    questions = [
        {
            "question_id": q[0], "content": q[1], "status": q[2], "style": q[3],
            "marks_max": q[4], "source_type": q[5], "source_id": q[6],
            "is_ai_generated": q[7], "question_group_id": q[8],
            "parent_question_id": q[9], "created_at": q[10],
        }
        for q in cur.fetchall()
    ]

    return {
        "paragraph_id": str(row[0]), "content": row[1], "source_document": row[2],
        "version": row[3], "status": row[4], "uploaded_at": row[5],
        "sentences": sentences, "sentence_count": len(sentences),
        "questions": questions, "question_count": len(questions),
    }


def list_documents(cur) -> list[dict[str, Any]]:
    """Distinct `source_document` values with a paragraph count each, newest
    upload first — backs the picker's document filter.

    Not paginated: this is a small distinct-value list (one entry per upload
    batch, not per paragraph), and a `Page` wrapper that could never usefully
    page would just be a second, unused offset/limit for callers to ignore.
    If the number of distinct documents ever grows large enough to need
    paging, that is the moment to add it — not before.
    """
    cur.execute("""
        SELECT source_document, COUNT(*), MAX(uploaded_at)
        FROM   paragraphs
        GROUP  BY source_document
        ORDER  BY MAX(uploaded_at) DESC, source_document
    """)
    return [
        {"source_document": r[0], "paragraph_count": r[1], "last_uploaded_at": r[2]}
        for r in cur.fetchall()
    ]


def supersede_paragraph(cur, *, paragraph_id) -> dict[str, Any]:
    """active -> superseded. Refuses (raises) rather than no-ops on a
    paragraph that is already superseded, or on one that does not exist —
    see ParagraphNotFoundError / ParagraphAlreadySupersededError.

    Superseding does not touch any question already generated from this
    paragraph: `questions.source_id` has no FK (it is polymorphic over
    `question_source_type`, same as every other source type), and a
    generated question's own review/publish lifecycle is independent of
    whether its source paragraph is later retired. It only stops the
    paragraph itself being offered for FUTURE generation
    (scripts/generate_questions.py refuses anything not status='active').
    """
    cur.execute(
        "SELECT status FROM paragraphs WHERE paragraph_id = %s",
        (str(paragraph_id),),
    )
    row = cur.fetchone()
    if row is None:
        raise ParagraphNotFoundError(f"No paragraph {paragraph_id}.")
    if row[0] != "active":
        raise ParagraphAlreadySupersededError(
            f"Paragraph {paragraph_id} is already status={row[0]!r}."
        )

    cur.execute(
        """
        UPDATE paragraphs SET status = 'superseded'
        WHERE  paragraph_id = %s
        RETURNING paragraph_id, content, source_document, version, status, uploaded_at
        """,
        (str(paragraph_id),),
    )
    result = cur.fetchone()
    return {
        "paragraph_id": str(result[0]), "content": result[1],
        "source_document": result[2], "version": result[3],
        "status": result[4], "uploaded_at": result[5],
    }
