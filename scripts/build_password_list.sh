#!/usr/bin/env bash
# Rebuild backend/adapters/src/tarn_adapters/auth/data/common-passwords.txt.gz from the NCSC
# top-100k list in SecLists (MIT License). See the NOTICE.md next to the output file.
set -euo pipefail
URL="https://raw.githubusercontent.com/danielmiessler/SecLists/master/Passwords/Common-Credentials/100k-most-used-passwords-NCSC.txt"
OUT="$(dirname "$0")/../backend/adapters/src/tarn_adapters/auth/data/common-passwords.txt.gz"
curl -fsSL "$URL" | tr -d '\r' | awk '{print tolower($0)}' | awk 'length($0) > 0' \
  | LC_ALL=C sort -u | gzip -9n > "$OUT"
echo "wrote $OUT ($(zcat "$OUT" | wc -l) passwords)"
