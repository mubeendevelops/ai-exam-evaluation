#!/usr/bin/env bash
# scripts/run_table_eval_demo.sh — one-command demo of the real
# evaluate_table_answer.py pipeline (real MinIO scan, real cell OCR via
# core/ocr_fallback.py — NOT --stub-extraction), using answer_blocks and a
# reference_asset that already exist in this project's DB.
#
# Mirrors scripts/run_diagram_eval_demo.sh, including its hardcoded-IDs
# approach and its --dry-run-by-default safety.
#
# IDs used (created on 2026-08-31 against this DB; both student scans are
# committed fixtures from media/tables/images/, uploaded to MinIO):
#   reference_asset_id: e2eb0fe8-c340-4c35-a201-eaa158439437
#     media/tables/reference_table.json, loaded by load_reference_table.py
#     and linked to question a0a0a0a0-0001-... via
#     question_asset_links(role='question_source') — required by
#     trg_evaluation_reference_matches_answer_question (migration 012).
#   SHIFTED   9dee422f-08d6-4c16-a845-daa6365e0e63
#     IndieFlower_shifted.png — reordered rows, a misspelled header, one
#     wrong number and one rounding-level one. Exercises all four
#     "wrote something" verdicts at once.
#   MISSING   6fa19f33-c42b-4794-a139-4ede43276d77
#     Caveat_missing_row.png — the SJF row omitted. Shows content_score
#     dropping well below content_score_on_aligned, which is the signal
#     that the SHAPE, not the values, is what went wrong.
#
# Requires MinIO running (docker compose -f docker-compose.minio.yml up -d).
#
# Usage:
#   ./scripts/run_table_eval_demo.sh            # both cases, dry run
#   ./scripts/run_table_eval_demo.sh --commit    # persists the shifted case

set -euo pipefail
cd "$(dirname "$0")/.."

set -a
source .env
set +a

source .venv-paddleocr/bin/activate

REFERENCE_ASSET_ID="e2eb0fe8-c340-4c35-a201-eaa158439437"
SHIFTED_BLOCK_ID="9dee422f-08d6-4c16-a845-daa6365e0e63"
MISSING_BLOCK_ID="6fa19f33-c42b-4794-a139-4ede43276d77"

if [[ "${1:-}" == "--commit" ]]; then
  echo ">>> Shifted rows / mixed verdicts — running for real (writes to evaluation_results)..."
  python3 scripts/evaluate_table_answer.py "$SHIFTED_BLOCK_ID" "$REFERENCE_ASSET_ID"
else
  echo ">>> Dry run (no DB writes) — pass --commit to actually persist the score."
  echo
  echo ">>> Case 1: shifted rows, misspelled header, wrong and rounding-level numbers"
  python3 scripts/evaluate_table_answer.py "$SHIFTED_BLOCK_ID" "$REFERENCE_ASSET_ID" --dry-run
  echo
  echo ">>> Case 2: a whole body row missing (structural vs content divergence)"
  python3 scripts/evaluate_table_answer.py "$MISSING_BLOCK_ID" "$REFERENCE_ASSET_ID" --dry-run
fi
