#!/usr/bin/env bash
# Rebuild backend/adapters/src/tarn_adapters/ocr/data/english-words.txt.gz, the English word
# list of the OCR selector's lexicon term (P10), from SCOWL 2020.12.07 (Spell Checker Oriented
# Word Lists, http://wordlist.aspell.net/). SCOWL's copyright notice permits use, copying,
# modification and distribution, provided the notice is kept: see NOTICE.md next to the output.
# Taken: sizes 10-50 of the english, american, british and proper-name (upper) lists; words of
# two or more letters (ASCII only), lower-cased; abbreviations and contractions left out.
set -euo pipefail
URL="https://downloads.sourceforge.net/project/wordlist/SCOWL/2020.12.07/scowl-2020.12.07.tar.gz"
SHA256="5587667caa20c4891390c2d42dbb4d5c4c3f41bee77af1457ece3ba23fb859cc"
OUT="$(cd "$(dirname "$0")/.." && pwd)/backend/adapters/src/tarn_adapters/ocr/data/english-words.txt.gz"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
curl -fsSL "$URL" -o "$WORK/scowl.tar.gz"
echo "$SHA256  $WORK/scowl.tar.gz" | sha256sum -c --quiet
tar -xzf "$WORK/scowl.tar.gz" -C "$WORK"
cd "$WORK"/scowl-*/final
FILES=()
for f in {english,american,british}-{words,upper}.{10,20,35,40,50}; do
  [[ -f "$f" ]] && FILES+=("$f")
done
cat "${FILES[@]}" \
  | iconv -f ISO-8859-1 -t UTF-8 \
  | tr -d '\r' \
  | grep -E "^[A-Za-z][A-Za-z'-]*[A-Za-z]$" \
  | awk '{print tolower($0)}' \
  | LC_ALL=C sort -u | gzip -9n > "$OUT"
echo "wrote $OUT ($(zcat "$OUT" | wc -l) words)"
