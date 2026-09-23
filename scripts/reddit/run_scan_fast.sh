#!/bin/bash
# Same as run_scan.sh, but filters with ripgrep instead of macOS BSD grep.
# BSD grep with -i on UTF-8 (Cyrillic) was the bottleneck: ~100% CPU,
# zstd waiting on it. ripgrep does the same case-insensitive match much faster.
# Requires: brew install ripgrep pv  (pv = progress bar over compressed bytes; optional)
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
