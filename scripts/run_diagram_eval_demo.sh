#!/usr/bin/env bash
# scripts/run_diagram_eval_demo.sh — one-command demo of the real
# evaluate_diagram_answer.py pipeline (real MinIO scan, real fallback OCR
# via core/ocr_fallback.py — NOT --stub-extraction), using an answer_block
# and reference_asset that already exist in this project's DB.
#
# IDs used (found via psql against this DB on 2026-08-28):
#   answer_block_id:    1d9115d4-cd43-4a53-9681-a303f4d2b0e7
#     blob_url: ai-evaluation/content-assets/test-scans/68d32aca-f24f-44b4-b59a-e5b5df70a7b4.jpeg
#   reference_asset_id: 1aef8f91-dc13-4f15-b1e1-e2df54df087a
#     linked to the same question (88880001-...) via
#     question_asset_links(role='question_source') — required by
#     trg_evaluation_reference_matches_answer_question (migration 012).
#
# --dry-run is used below so this is safe to re-run repeatedly without
# writing a new evaluation_results row each time — drop --dry-run once
# you're ready to actually persist a scored result.
#
# Usage:
#   ./scripts/run_diagram_eval_demo.sh          # dry run (no DB writes)
#   ./scripts/run_diagram_eval_demo.sh --commit  # actually writes the result

set -euo pipefail
cd "$(dirname "$0")/.."

set -a
source .env
set +a

source .venv-paddleocr/bin/activate

ANSWER_BLOCK_ID="1d9115d4-cd43-4a53-9681-a303f4d2b0e7"
REFERENCE_ASSET_ID="1aef8f91-dc13-4f15-b1e1-e2df54df087a"

if [[ "${1:-}" == "--commit" ]]; then
  echo ">>> Running for real (writes to evaluation_results)..."
  python3 scripts/evaluate_diagram_answer.py "$ANSWER_BLOCK_ID" "$REFERENCE_ASSET_ID"
else
  echo ">>> Dry run (no DB writes) — pass --commit to actually persist the score."
  python3 scripts/evaluate_diagram_answer.py "$ANSWER_BLOCK_ID" "$REFERENCE_ASSET_ID" --dry-run
fi
