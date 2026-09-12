"""core/block_evaluation.py — score ONE table/diagram answer_block against ONE
explicitly named reference asset, and the per-type reference plumbing that
core/booklet_evaluator.py shares with it.

scripts/evaluate_table_answer.py and scripts/evaluate_diagram_answer.py used
to carry two copies of evaluate_block() below, identical except for the
block type, the plugin name and the reference dataclass (CLEANUP_AUDIT.md
§2). Those differences are now the `_PLUGINS` table and asset_reference(),
so a third asset-backed block type is one row here rather than a third
copy-pasted script body (§5 rule 3). The scripts keep their argument
parsing, their terminal rendering and their return shapes; they are front
doors onto this (§11 rule 1).

Plugin imports are deferred to first call, as in core/booklet_evaluator.py:
importing this module must not load OCR or embedding models.
"""
from __future__ import annotations

import inspect

#: block_type -> the plugin that extracts and scores it. The block_type is
#: also the content_assets.asset_type its reference must have.
_PLUGINS = {
    "table": "table_extraction",
    "diagram": "diagram_evaluation",
}


def load_glossary(cur, topic_id: str | None = None) -> list[dict]:
    """glossary_terms, for diagram label canonicalization.

    With `topic_id`, that topic's terms plus the global (topic-less) ones;
    without, every term. glossary_terms is shared across colleges and has no
    college_id and no RLS (CLAUDE_CONTEXT.md §5), so this works on any
    connection.
    """
    if topic_id:
        cur.execute("""
            SELECT term_id, canonical_term, aliases FROM glossary_terms
            WHERE topic_id = %s OR topic_id IS NULL
        """, (topic_id,))
    else:
        cur.execute("SELECT term_id, canonical_term, aliases FROM glossary_terms")
    return [
        {"term_id": str(term_id), "canonical_term": term, "aliases": aliases or []}
        for term_id, term, aliases in cur.fetchall()
    ]


def asset_reference(block_type: str, structured_data, *, marks_max: float,
                    asset_id: str, glossary: list | None = None):
    """A content_assets row's structured_data as the matching plugin's
    reference dataclass. `glossary` is used by diagrams only."""
    if block_type == "table":
        from core.plugins.table_extraction import TableReference
        return TableReference(table=structured_data, marks_max=marks_max,
                              asset_id=asset_id)
    if block_type == "diagram":
        from core.plugins.diagram_evaluation import DiagramReference
        return DiagramReference(graph=structured_data, marks_max=marks_max,
                                glossary_terms=glossary or [], asset_id=asset_id)
    raise ValueError(f"block_type={block_type!r} has no asset-backed reference")


def evaluate_block(conn, answer_block_id: str, reference_asset_id: str, *,
                   block_type: str, stub: bool = False, stub_extraction: bool = False,
                   dry_run: bool = False, topic_id: str | None = None,
                   explain: bool = False, stub_llm: bool = False) -> dict:
    """Score one `block_type` answer_block against one reference asset.

    Takes a CONNECTION, not a cursor: the ledger write is delegated to
    core/plugins/persistence.write_evaluation_result(), which owns the
    commit/rollback (including the dry_run rollback). The connection's
    transaction must already have RLS bypassed (platform admin) or the
    correct college_id set — this function doesn't set it.

    `topic_id` scopes the glossary (diagrams only). `explain`/`stub_llm` are
    offered only to plugins whose evaluate() declares them — the same
    signature rule core/booklet_evaluator.py::_evaluate_kwargs uses.

    The trigger trg_evaluation_reference_matches_answer_question (migration
    012) enforces that the reference asset is linked to the answer's
    question via question_asset_links(role='question_source'); no
    application-level check is needed here.
    """
    from core import answer_evaluation
    from core.plugins import persistence
    from core.plugins.registry import get_plugin

    plugin_name = _PLUGINS.get(block_type)
    if plugin_name is None:
        raise ValueError(f"block_type={block_type!r} has no single-block evaluator")

    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT ab.answer_id, ab.blob_url, ab.block_type, a.status, a.question_id
            FROM answer_blocks ab
            JOIN answers a ON a.answer_id = ab.answer_id
            WHERE ab.block_id = %s
        """, (answer_block_id,))
        row = cur.fetchone()
        if row is None:
            # persistence.write_evaluation_result() raises RLSVisibilityError for
            # exactly this case with a full diagnostic; reuse it rather than
            # reporting "not found" for what is usually a missing tenant context.
            raise persistence.RLSVisibilityError(
                f"answer_block {answer_block_id!r} returned zero rows. answer_blocks is "
                f"RLS-protected and fails closed SILENTLY, so this is EITHER a "
                f"nonexistent block_id OR a missing tenant context on this transaction "
                f"(see core/plugins/persistence.py's module docstring, rule 4)."
            )
        answer_id, blob_url, actual_block_type, answer_status, question_id = row

        if actual_block_type != block_type:
            raise ValueError(
                f"answer_block {answer_block_id} has block_type={actual_block_type!r}, "
                f"expected {block_type!r}"
            )
        # A stub extraction never reads the image, so only a real one needs it.
        if not blob_url and not (stub or stub_extraction):
            raise ValueError(
                f"answer_block {answer_block_id} has no blob_url — cannot extract a "
                f"{block_type} from nothing. Use --stub-extraction for test data."
            )

        cur.execute(
            "SELECT asset_type, structured_data FROM content_assets WHERE asset_id = %s",
            (reference_asset_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"reference_asset_id {reference_asset_id} not found")
        asset_type, structured_data = row
        if asset_type != block_type:
            raise ValueError(
                f"content_asset {reference_asset_id} has asset_type={asset_type!r}, "
                f"expected {block_type!r}"
            )
        if not structured_data:
            raise ValueError(
                f"content_asset {reference_asset_id} has no structured_data to compare against"
            )

        cur.execute("SELECT marks_max FROM questions WHERE question_id = %s", (question_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"question_id {question_id} not found")
        marks_max = float(row[0])

        # --stub never looks at a glossary (stub_compare() ignores it), so it
        # skips the query rather than doing work whose result is discarded.
        glossary = (load_glossary(cur, topic_id=topic_id)
                    if block_type == "diagram" and not stub else [])
        reference = asset_reference(block_type, structured_data, marks_max=marks_max,
                                    asset_id=reference_asset_id, glossary=glossary)

        plugin = get_plugin(plugin_name)
        parameters = inspect.signature(plugin.evaluate).parameters
        evaluate_kwargs: dict = {"stub": stub}
        if "explain" in parameters:
            evaluate_kwargs["explain"] = explain
        if "stub_llm" in parameters:
            evaluate_kwargs["stub_llm"] = stub_llm

        extracted = plugin.extract(blob_url, stub=stub or stub_extraction)
        result = plugin.evaluate(extracted, reference, **evaluate_kwargs)

        # Issued BEFORE the ledger write so both land in the same
        # transaction — write_evaluation_result() owns the commit.
        status_changed = answer_evaluation.transition_to_ai_scored(cur, answer_id, answer_status)
    finally:
        cur.close()

    evaluation_id = persistence.write_evaluation_result(
        conn,
        answer_block_id=answer_block_id,
        result=result,
        reference_asset_id=reference_asset_id,
        dry_run=dry_run,
    )

    return {
        "answer_id": answer_id,
        "evaluation_id": evaluation_id,
        "reference_asset_id": reference_asset_id,
        "score": result.score,
        "marks_max": marks_max,
        "confidence": result.confidence,
        "explanation": result.explanation,
        "metrics": result.metrics,
        "status_changed": status_changed,
    }
