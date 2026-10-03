#!/usr/bin/env bash
# Cut the reference diagrams used by the development seed out of the faculty keys K-AI1 and
# K-AI3 (samples/, git-ignored). The keys hold no student data. The PNGs are committed under
# backend/adapters/src/tarn_adapters/seed/data/, so only whoever changes them needs this.
# Needs poppler-utils (pdfimages). Image numbers come from `pdfimages -list`.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=backend/adapters/src/tarn_adapters/seed/data
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

pdfimages -png -p samples/AIML_Internal_Question_Paper_1_Key.pdf "$TMP/k1"
pdfimages -png -p samples/AIML_Internal_Question_Paper_3_Key.pdf "$TMP/k3"

cp "$TMP/k1-002-002.png" "$OUT/k-ai1-q2a-unit.png"            # page 2: the unit (neuron)
cp "$TMP/k1-003-006.png" "$OUT/k-ai1-q2b-network.png"         # page 3: 2-2-1 network
cp "$TMP/k1-006-009.png" "$OUT/k-ai1-q5-dendrogram.png"       # page 6: AGNES dendrogram
cp "$TMP/k1-008-012.png" "$OUT/k-ai1-q7a-vector-form.png"     # page 8: layers in vector form
cp "$TMP/k1-010-014.png" "$OUT/k-ai1-q8a-rnn.png"             # page 10: simple RNN
cp "$TMP/k3-004-002.png" "$OUT/k-ai3-q4-model-based-agent.png"   # page 4
cp "$TMP/k3-004-003.png" "$OUT/k-ai3-q4-utility-based-agent.png" # page 4
ls -l "$OUT"
