#!/bin/bash
# Stream-scan one monthly submissions dump without decompressing it to disk.
# Usage: scripts/reddit/run_scan.sh <RS_YYYY-MM.zst> <out.csv>
set -euo pipefail
IN="$1"; OUT="$2"
zstd -dc --long=31 "$IN" | python3 "$(dirname "$0")/scan_fast.py" > "$OUT"
