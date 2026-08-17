#!/usr/bin/env python3
"""
load_exam_bank.py — populates the Question schema (questions,
reference_answer_variants, content_assets, question_asset_links,
keywords, question_keywords) from the JSON produced by
extract_exam_bank.py.

This only touches Question-schema tables. exam_bank.docx is a question
bank (questions + reference answers), not student submissions — there is
no `answers` data here, so `students`/`exams`/`answers` are untouched.

Idempotent: every UUID is derived deterministically (uuid5) from a stable
source key (e.g. "question:8"), so re-running this script against the same
JSON upserts instead of duplicating rows or re-uploading the same image to
MinIO twice.

Usage:
    export PGHOST=localhost PGDATABASE=exam_platform PGUSER=postgres PGPASSWORD=...
    python3 scripts/load_exam_bank.py extracted/exam_bank.json --status draft
    # --storage defaults to "dummy": no MinIO needed, blob_url is a placeholder.
    # Switch to real uploads later with --storage minio (needs boto3 + MINIO_* env vars).

    python3 scripts/load_exam_bank.py extracted/exam_bank.json --dry-run   # no DB calls at all, just prints the plan
"""
import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db as db_mod   # noqa: E402
from core import storage        # noqa: E402

NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "examplatform.local")

# Doc's 3-tier grouping doesn't map 1:1 onto the `question_style` enum
# (long|short|one_word|mcq). Mapping chosen here; the original tier is
# preserved separately as a keyword so the distinction isn't lost.
TIER_TO_STYLE = {
    "short-answer": "short",
    "medium-length": "long",
    "complex-long-answer": "long",
}
TIER_TO_VARIANT_TYPE = {
    "short-answer": "short",
    "medium-length": "long",
    "complex-long-answer": "long",
}
# No marks data in the source doc — placeholder defaults by tier, meant to
# be corrected by whoever owns the question bank before these go live.
TIER_TO_MARKS_MAX = {
    "short-answer": 2,
    "medium-length": 5,
    "complex-long-answer": 10,
}


def det_uuid(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, name))


def build_plan(questions: list[dict], status: str) -> list[dict]:
    """Turn parsed questions into a flat list of row-insert operations,
    all IDs pre-resolved. Kept separate from execution so --dry-run can
    print exactly what would happen."""
    plan = []
    now = datetime.now(timezone.utc).isoformat()

    for q in questions:
        idx = q["index"]
        tier = q["tier"]
        question_id = det_uuid(f"question:{idx}")
        group_id = det_uuid(f"question_group:{idx}")
        variant_id = det_uuid(f"variant:{idx}")
        keyword_id = det_uuid(f"keyword:tier:{tier}")

        question_row = {
            "question_id": question_id,
            "source_type": "manual",
            "source_id": None,
            "style": TIER_TO_STYLE[tier],
            "marks_max": TIER_TO_MARKS_MAX[tier],
            "status": status,
            "supersedes_question_id": None,
            "question_group_id": group_id,
            "created_at": now,
            "content": q["question_text"],
        }

        keyword_row = {"keyword_id": keyword_id, "term": tier}
        question_keyword_row = {"question_id": question_id, "keyword_id": keyword_id, "weight": 1.0}

        variant_row = {
            "variant_id": variant_id,
            "question_id": question_id,
            "variant_type": TIER_TO_VARIANT_TYPE[tier],
            "content": q["answer_text"],
            "is_current": True,
            "created_at": now,
        }

        asset_rows = []
        link_rows = []

        for t_idx, table in enumerate(q["tables"]):
            asset_id = det_uuid(f"asset:table:{idx}:{t_idx}")
            asset_rows.append({
                "asset_id": asset_id,
                "asset_type": "table",
                "blob_url": None,
                "structured_data": table,   # {"headers": [...], "rows": [[...]]}
                "uploaded_at": now,
            })
            link_rows.append({
                "link_id": det_uuid(f"link:table:{idx}:{t_idx}"),
                "asset_id": asset_id,
                "role": "answer_component",
                "question_id": None,
                "reference_answer_variant_id": variant_id,
            })

        for i_idx, image in enumerate(q["images"]):
            asset_id = det_uuid(f"asset:diagram:{idx}:{i_idx}")
            asset_rows.append({
                "asset_id": asset_id,
                "asset_type": "diagram",
                "blob_url": None,   # filled in at upload time (see upload_pending_diagrams)
                "structured_data": None,
                "uploaded_at": now,
                "_local_path": image["local_path"],
            })
            link_rows.append({
                "link_id": det_uuid(f"link:diagram:{idx}:{i_idx}"),
                "asset_id": asset_id,
                "role": "answer_component",
                "question_id": None,
                "reference_answer_variant_id": variant_id,
            })

        plan.append({
            "question": question_row,
            "keyword": keyword_row,
            "question_keyword": question_keyword_row,
            "variant": variant_row,
            "assets": asset_rows,
            "links": link_rows,
        })

    return plan


def upload_pending_diagrams(plan: list[dict], cur=None, dry_run: bool = False, storage_mode: str = "dummy"):
    """Resolves blob_url for every diagram asset, either as a deterministic
    placeholder ("dummy" mode — no I/O, no MinIO needed) or a real MinIO
    upload ("minio" mode, idempotent: skips re-upload if this asset_id
    already has a blob_url in content_assets)."""
    for entry in plan:
        for asset in entry["assets"]:
            if asset["asset_type"] != "diagram":
                continue

            if storage_mode == "dummy":
                asset["blob_url"] = storage.dummy_upload(
                    asset["_local_path"], "content-assets/diagrams", asset["asset_id"]
                )
                tag = "[dry-run] " if dry_run else ""
                print(f"  {tag}dummy blob_url for asset {asset['asset_id']}: {asset['blob_url']}")
                continue

            # storage_mode == "minio"
            if dry_run:
                print(f"  [dry-run] would upload {asset['_local_path']} -> "
                      f"MinIO, asset_id={asset['asset_id']}")
                asset["blob_url"] = "<dry-run: not uploaded>"
                continue

            cur.execute("SELECT blob_url FROM content_assets WHERE asset_id = %s", (asset["asset_id"],))
            existing = cur.fetchone()
            if existing:
                asset["blob_url"] = existing[0]
                print(f"  asset {asset['asset_id']} already uploaded, reusing {asset['blob_url']}")
                continue

            blob_url = storage.upload_file(
                asset["_local_path"],
                key_prefix="content-assets/diagrams",
                content_type="image/jpeg",
            )
            asset["blob_url"] = blob_url
            print(f"  uploaded {asset['_local_path']} -> {blob_url}")


def execute_plan(plan: list[dict], cur):
    for entry in plan:
        q = entry["question"]
        cur.execute("""
            INSERT INTO questions (question_id, source_type, source_id, style, marks_max,
                                    status, supersedes_question_id, question_group_id, created_at, content)
            VALUES (%(question_id)s, %(source_type)s, %(source_id)s, %(style)s, %(marks_max)s,
                    %(status)s, %(supersedes_question_id)s, %(question_group_id)s, %(created_at)s, %(content)s)
            ON CONFLICT (question_id) DO UPDATE SET
                style = EXCLUDED.style, marks_max = EXCLUDED.marks_max, status = EXCLUDED.status,
                content = EXCLUDED.content
        """, q)

        kw = entry["keyword"]
        cur.execute("""
            INSERT INTO keywords (keyword_id, term) VALUES (%(keyword_id)s, %(term)s)
            ON CONFLICT (term) DO NOTHING
        """, kw)
        # term is UNIQUE, so on conflict we need the *existing* keyword_id for the link below
        cur.execute("SELECT keyword_id FROM keywords WHERE term = %s", (kw["term"],))
        real_keyword_id = cur.fetchone()[0]

        qk = entry["question_keyword"]
        cur.execute("""
            INSERT INTO question_keywords (question_id, keyword_id, weight)
            VALUES (%s, %s, %s)
            ON CONFLICT (question_id, keyword_id) DO UPDATE SET weight = EXCLUDED.weight
        """, (qk["question_id"], real_keyword_id, qk["weight"]))

        v = entry["variant"]
        cur.execute("""
            INSERT INTO reference_answer_variants
                (variant_id, question_id, variant_type, content, is_current, created_at)
            VALUES (%(variant_id)s, %(question_id)s, %(variant_type)s, %(content)s,
                    %(is_current)s, %(created_at)s)
            ON CONFLICT (variant_id) DO UPDATE SET
                content = EXCLUDED.content, is_current = EXCLUDED.is_current
        """, v)

        for asset in entry["assets"]:
            cur.execute("""
                INSERT INTO content_assets (asset_id, asset_type, blob_url, structured_data, uploaded_at)
                VALUES (%(asset_id)s, %(asset_type)s, %(blob_url)s, %(structured_data)s, %(uploaded_at)s)
                ON CONFLICT (asset_id) DO UPDATE SET
                    blob_url = EXCLUDED.blob_url, structured_data = EXCLUDED.structured_data
            """, {
                "asset_id": asset["asset_id"],
                "asset_type": asset["asset_type"],
                "blob_url": asset["blob_url"],
                "structured_data": json.dumps(asset["structured_data"]) if asset["structured_data"] else None,
                "uploaded_at": asset["uploaded_at"],
            })

        for link in entry["links"]:
            cur.execute("""
                INSERT INTO question_asset_links
                    (link_id, asset_id, role, question_id, reference_answer_variant_id)
                VALUES (%(link_id)s, %(asset_id)s, %(role)s, %(question_id)s, %(reference_answer_variant_id)s)
                ON CONFLICT (link_id) DO NOTHING
            """, link)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json_path", type=Path)
    ap.add_argument("--status", default="draft", choices=["draft", "confirmed", "rejected", "live", "superseded"],
                     help="status to import questions with (default: draft, pending human review)")
    ap.add_argument("--storage", default="dummy", choices=["dummy", "minio"],
                     help="'dummy' (default): no object storage needed, blob_url is a deterministic "
                          "placeholder. 'minio': actually upload diagrams to MinIO (needs boto3 + "
                          "MINIO_* env vars).")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, touch neither DB nor MinIO")
    args = ap.parse_args()

    questions = json.loads(args.json_path.read_text())
    plan = build_plan(questions, status=args.status)

    print(f"Loaded {len(plan)} questions from {args.json_path} (status={args.status}, storage={args.storage})")

    if args.dry_run:
        upload_pending_diagrams(plan, dry_run=True, storage_mode=args.storage)
        for entry in plan:
            q = entry["question"]
            print(f"  question_id={q['question_id']} style={q['style']} marks_max={q['marks_max']} "
                  f"assets={len(entry['assets'])} text={q['content'][:60]!r}...")
        print("[dry-run] no DB writes performed.")
        return

    conn = db_mod.get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                upload_pending_diagrams(plan, cur=cur, dry_run=False, storage_mode=args.storage)
                execute_plan(plan, cur)
        print("Committed.")
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
