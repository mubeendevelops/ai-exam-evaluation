"""core/reference_assets.py — writes one hand-authored reference asset
(content_assets row + its optional question_asset_links row).

scripts/load_reference_diagram.py and scripts/load_reference_table.py used
to carry two copies of this INSERT and its FK-existence checks
(CLEANUP_AUDIT.md §2). What differs per type — validating a graph vs a grid
— stays in each script; what does not is here, once (§5 rule 3, and the
same reason content_assets itself is one generalized table rather than
reference_diagrams/reference_tables).

No RLS context is needed: content_assets and question_asset_links belong to
the shared question schema, which has no college_id (CLAUDE_CONTEXT.md §5).
"""
from __future__ import annotations

import json
import uuid

#: Substrings that mark a URL as presigned/temporary rather than a stable
#: "bucket/key" reference. Checked case-insensitively.
_PRESIGNED_MARKERS = ("x-amz-signature", "x-amz-credential", "signature=", "?", "://")


def validate_blob_url(blob_url: str) -> None:
    """A blob_url must be the stable "bucket/key" form this repo stores
    everywhere (CLAUDE_CONTEXT.md §10). Rejects a presigned/absolute URL
    with a message that says what to store instead — silently accepting one
    would put an expiring link in the DB that reads as valid until it
    isn't."""
    lowered = blob_url.lower()
    for marker in _PRESIGNED_MARKERS:
        if marker in lowered:
            raise ValueError(
                f"blob_url {blob_url!r} looks like a presigned or absolute URL "
                f"(contains {marker!r}). content_assets.blob_url must be a stable "
                f'"bucket/key" reference, e.g. "reference-assets/tables/cpu-sched.png" '
                f"— presigned URLs expire and are generated on demand via "
                f"core/storage.py::presigned_get_url()."
            )
    if "/" not in blob_url.strip("/"):
        raise ValueError(
            f'blob_url {blob_url!r} is not in "bucket/key" form — it needs a bucket '
            f"and a key separated by '/'."
        )


def insert_reference_asset(cur, asset_type: str, structured_data: dict, *,
                           question_id: str | None = None,
                           variant_id: str | None = None,
                           blob_url: str | None = None) -> str:
    """Inserts one content_assets row holding `structured_data`, optionally
    linked via question_asset_links — to a question as
    role='question_source', or to a reference answer variant as
    role='answer_component'; at most one of the two. Caller owns the
    transaction. Returns the new asset_id.

    Everything that can refuse the call is checked BEFORE the first INSERT,
    so a refused call writes nothing even for a caller that does not roll
    back. `structured_data` is written as given; validating its shape is the
    caller's job, since that is the per-type half.
    """
    if question_id and variant_id:
        raise ValueError("pass at most one of question_id / variant_id")
    if blob_url:
        validate_blob_url(blob_url)
    if question_id:
        cur.execute("SELECT question_id FROM questions WHERE question_id = %s", (question_id,))
        if cur.fetchone() is None:
            raise ValueError(f"question_id {question_id} not found")
    if variant_id:
        cur.execute("SELECT variant_id FROM reference_answer_variants WHERE variant_id = %s",
                    (variant_id,))
        if cur.fetchone() is None:
            raise ValueError(f"variant_id {variant_id} not found")

    asset_id = str(uuid.uuid4())
    cur.execute("""
        INSERT INTO content_assets (asset_id, asset_type, blob_url, structured_data, uploaded_at)
        VALUES (%s, %s, %s, %s, now())
    """, (asset_id, asset_type, blob_url, json.dumps(structured_data)))

    if question_id or variant_id:
        role = "question_source" if question_id else "answer_component"
        cur.execute("""
            INSERT INTO question_asset_links (link_id, asset_id, role, question_id, reference_answer_variant_id)
            VALUES (%s, %s, %s, %s, %s)
        """, (str(uuid.uuid4()), asset_id, role, question_id, variant_id))

    return asset_id
