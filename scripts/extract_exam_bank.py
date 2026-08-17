#!/usr/bin/env python3
"""
extract_exam_bank.py

Parses exam_bank.docx into a structured JSON file (questions, reference
answers, embedded tables, and embedded diagram images) ready for
load_exam_bank.py to push into Postgres + MinIO.

Usage:
    python3 extract_exam_bank.py exam_bank.docx --outdir ./extracted
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Section headers in the doc -> a short tier tag we keep as a question_keyword
# (there's no free-text "tier" column on `questions`, so this is stored via
# the existing question_keywords mechanism instead of inventing a new column)
SECTION_TIERS = {
    "short-answer questions": "short-answer",
    "medium-length questions": "medium-length",
    "complex long-answer questions": "complex-long-answer",
}

QUESTION_HEADER_RE = re.compile(r"^question\s+(\d+)$", re.IGNORECASE)
ANSWER_HEADER_RE = re.compile(r"^answer:?$", re.IGNORECASE)


def inlines_to_text(inlines):
    """Render a list of pandoc inline nodes to plain text, keeping LaTeX
    for Math nodes (wrapped in $...$ / $$...$$ so it's still readable as
    text and re-parseable later if needed)."""
    parts = []
    for node in inlines:
        t = node.get("t")
        if t == "Str":
            parts.append(node["c"])
        elif t in ("Space", "SoftBreak"):
            parts.append(" ")
        elif t == "LineBreak":
            parts.append("\n")
        elif t in ("Strong", "Emph", "Underline"):
            parts.append(inlines_to_text(node["c"]))
        elif t == "Code":
            parts.append(node["c"][1])
        elif t == "Math":
            math_type, latex = node["c"]
            if math_type.get("t") == "DisplayMath":
                parts.append(f"\n$$ {latex} $$\n")
            else:
                parts.append(f"${latex}$")
        elif t == "Quoted":
            inner = inlines_to_text(node["c"][1])
            parts.append(f'"{inner}"')
        elif t in ("Superscript", "Subscript"):
            parts.append(inlines_to_text(node["c"]))
        elif t == "Note":
            pass  # footnotes not relevant here
        # anything else (Link, Image inline, etc.) is handled separately
        # by the block walker, not inlined into plain text
    return "".join(parts).strip()


def block_to_text(block):
    t = block.get("t")
    if t == "Para" or t == "Plain":
        return inlines_to_text(block["c"])
    if t in ("BulletList", "OrderedList"):
        items = block["c"] if t == "BulletList" else block["c"][1]
        lines = []
        for item_blocks in items:
            item_text = "\n".join(block_to_text(b) for b in item_blocks if block_to_text(b))
            lines.append(f"- {item_text}")
        return "\n".join(lines)
    return ""


def find_images_in_block(block):
    """Return list of (alt_text, src_path) for any Image inline anywhere
    in this block."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("t") == "Image":
                attrs, alt_inlines, target = node["c"]
                alt_text = inlines_to_text(alt_inlines)
                src_path = target[0]
                found.append((alt_text, src_path))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(block)
    return found


def table_block_to_dict(block):
    """Handle both pandoc Table AST formats:
    - Pandoc >= 2.17 (api 1.23): [Attr, Caption, [ColSpec], TableHead, [TableBody], TableFoot]
    - Pandoc < 2.17 (old format): [caption, [Alignment], [width], headers_cells, rows]
    """
    parts = block["c"]

    if len(parts) == 6:
        # New format (pandoc >= 2.17)
        _, _, _, table_head, table_bodies, _ = parts

        def row_to_texts(row):
            _, cells = row
            texts = []
            for cell in cells:
                _, _, _, _, cell_blocks = cell
                cell_text = " ".join(block_to_text(b) for b in cell_blocks if block_to_text(b))
                texts.append(cell_text.strip())
            return texts

        headers = []
        _, head_rows = table_head
        if head_rows:
            headers = row_to_texts(head_rows[0])

        rows = []
        for body in table_bodies:
            _, _, intermediate_head_rows, body_rows = body
            for r in intermediate_head_rows:
                rows.append(row_to_texts(r))
            for r in body_rows:
                rows.append(row_to_texts(r))

    else:
        # Old format (pandoc < 2.17): [caption, [Alignment], [width], header_cells, rows]
        _, _, _, header_cells, body_rows = parts

        def cells_to_texts(cells):
            """Old format cells are just lists of blocks (no Attr wrapper)."""
            texts = []
            for cell in cells:
                cell_text = " ".join(block_to_text(b) for b in cell if block_to_text(b))
                texts.append(cell_text.strip())
            return texts

        headers = cells_to_texts(header_cells) if header_cells else []
        rows = [cells_to_texts(row) for row in body_rows]

    return {"headers": headers, "rows": rows}


def parse_blocks(blocks):
    """Walk the flat block list, grouping into questions using the
    'Question N' / 'Answer:' markers, and tagging each with its section tier."""
    questions = []
    current_tier = None
    current = None  # in-progress question dict
    mode = None  # None -> 'awaiting_question_text' -> 'awaiting_answer' -> 'in_answer'

    def flush():
        if current is not None:
            current["answer_text"] = current["answer_text"].strip()
            questions.append(current)

    for block in blocks:
        text = block_to_text(block).strip() if block.get("t") in ("Para", "Plain") else None

        if text is not None:
            lowered = text.lower()
            if lowered in SECTION_TIERS:
                current_tier = SECTION_TIERS[lowered]
                continue

            m = QUESTION_HEADER_RE.match(text)
            if m:
                flush()
                current = {
                    "index": int(m.group(1)),
                    "tier": current_tier,
                    "question_text": "",
                    "answer_text": "",
                    "tables": [],
                    "images": [],
                }
                mode = "awaiting_question_text"
                continue

            if ANSWER_HEADER_RE.match(text):
                mode = "in_answer"
                continue

        if current is None:
            continue  # front matter before Question 1

        if mode == "awaiting_question_text":
            piece = block_to_text(block)
            if piece:
                current["question_text"] = (current["question_text"] + " " + piece).strip()
            continue

        if mode == "in_answer":
            if block.get("t") == "Table":
                current["tables"].append(table_block_to_dict(block))
                continue
            images = find_images_in_block(block)
            for alt_text, src_path in images:
                current["images"].append({"alt_text": alt_text, "src_path": src_path})
            piece = block_to_text(block)
            if piece:
                current["answer_text"] += ("\n\n" if current["answer_text"] else "") + piece

    flush()
    return questions


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("docx_path", type=Path)
    ap.add_argument("--outdir", type=Path, default=Path("./extracted"))
    args = ap.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)
    media_dir = args.outdir / "media"

    # 1. Ask pandoc for the AST and extract embedded media alongside it.
    ast_bytes = subprocess.run(
        ["pandoc", "-t", "json", f"--extract-media={media_dir}", str(args.docx_path)],
        capture_output=True, check=True,
    ).stdout
    doc = json.loads(ast_bytes)

    # 2. Parse into question records.
    questions = parse_blocks(doc["blocks"])

    # 3. Resolve image src_path to an absolute path we can hand to the loader
    # for upload. Pandoc already returns it relative to the current working
    # directory (not relative to --extract-media's target), since we passed
    # a directory prefix to --extract-media.
    for q in questions:
        for img in q["images"]:
            img["local_path"] = str(Path(img["src_path"]).resolve())

    out_path = args.outdir / "exam_bank.json"
    out_path.write_text(json.dumps(questions, indent=2))

    print(f"Parsed {len(questions)} questions -> {out_path}")
    for q in questions:
        print(f"  Q{q['index']} [{q['tier']}] tables={len(q['tables'])} images={len(q['images'])} "
              f"question_len={len(q['question_text'])} answer_len={len(q['answer_text'])}")


if __name__ == "__main__":
    sys.exit(main())
