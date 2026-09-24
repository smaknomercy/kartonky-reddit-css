#!/bin/bash
# Step 3a: count keyword hits per subreddit in one monthly submissions dump,
# streaming (the .zst is never decompressed to disk).
#   zstd -dc  ->  ripgrep keyword prefilter  ->  scan_subreddits.py (exact count per subreddit)
# ripgrep, not grep: macOS BSD grep with -i on Cyrillic ran at ~100% CPU and
# made a 23 GB dump take hours; ripgrep does it in ~6 min.
# Usage:    scripts/reddit/run_scan.sh <RS_YYYY-MM.zst> <out.csv>
# Requires: zstd, ripgrep; pv (optional, progress bar)  ->  brew install zstd ripgrep pv
set -euo pipefail
IN="$1"; OUT="$2"
PATTERN='fedorov|федоров|protest|протест|мітинг|картонк|ukrain|україн|украин|kyiv|kiev|київ|киев|zelensk|зеленськ'
start=$(date +%s)
NAME="$(basename "$IN" .zst)"
if command -v pv >/dev/null; then
  READ=(pv -N "$NAME" -pterab "$IN")
else
  READ=(cat "$IN")
fi
"${READ[@]}" | zstd -dc --long=31 | rg -i --no-line-number "$PATTERN" \
  | python3 "$(dirname "$0")/scan_subreddits.py" > "$OUT"
echo "done $IN in $(( $(date +%s) - start ))s"
