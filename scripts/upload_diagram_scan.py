#!/usr/bin/env python3
"""
scripts/upload_diagram_scan.py — Task 4 testing helper: uploads a local
image file (a real photo/scan of a handwritten diagram) to real object
storage and attaches it to an existing answer as a new answer_blocks row,
so scripts/evaluate_diagram_answer.py has something real to read.

This exists because testing real (non-stub) extraction needs an actual
image behind a blob_url — the seed data (migrations/seed_diagram_test_data.sql)
deliberately uses a "dummy-storage/..." placeholder, same as every other
seed script in this repo, since no real scan file ships with the repo.

Requires MinIO running (docker compose -f docker-compose.minio.yml up -d)
and MINIO_* env vars set (see .env.example) — this is real-storage-only,
there is no --storage dummy mode here (a dummy blob_url has no image behind
it to run extraction against, which defeats the point of this script).

Usage:
    export PGHOST=... PGDATABASE=... PGUSER=... PGPASSWORD=...
    export MINIO_ENDPOINT=... MINIO_ACCESS_KEY=... MINIO_SECRET_KEY=... MINIO_BUCKET=...

    python3 scripts/upload_diagram_scan.py my_photo.jpg --answer-id <uuid>
"""
import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db as db_mod   # noqa: E402
from core import storage        # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("image_path", type=Path)
    ap.add_argument("--answer-id", required=True,
                    help="existing answers.answer_id to attach this scan to "
                         "(e.g. the seeded 99990020-0020-0020-0020-999900209999)")
    args = ap.parse_args()

    if not args.image_path.exists():
        print(f"ERROR: {args.image_path} does not exist", file=sys.stderr)
        sys.exit(1)

    content_type = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    }.get(args.image_path.suffix.lower(), "application/octet-stream")

    blob_url = storage.upload_file(
        str(args.image_path), key_prefix="content-assets/test-scans", content_type=content_type,
    )
    print(f"Uploaded {args.image_path} -> {blob_url}")

    conn = db_mod.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL app.is_platform_admin = 'true'")

            cur.execute("SELECT answer_id FROM answers WHERE answer_id = %s", (args.answer_id,))
            if cur.fetchone() is None:
                raise ValueError(f"answer_id {args.answer_id} not found")

            cur.execute(
                "SELECT COALESCE(MAX(sequence_order), 0) + 1 FROM answer_blocks WHERE answer_id = %s",
                (args.answer_id,),
            )
            next_order = cur.fetchone()[0]

            block_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO answer_blocks (block_id, answer_id, block_type, blob_url, content, sequence_order)
                VALUES (%s, %s, 'diagram', %s, NULL, %s)
            """, (block_id, args.answer_id, blob_url, next_order))

            cur.execute("UPDATE answers SET status = 'pending_evaluation' WHERE answer_id = %s",
                        (args.answer_id,))

        conn.commit()
        print(f"Created answer_block {block_id} (answer set back to pending_evaluation)")
        print(f"\nNow run:\n  python3 scripts/evaluate_diagram_answer.py {block_id} <reference_asset_id>")
    except ValueError as e:
        conn.rollback()
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
