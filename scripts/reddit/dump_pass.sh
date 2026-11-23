#!/bin/bash
# One streaming pass over a monthly Reddit dump, to keep what we may need
# after the .zst is deleted. The file is read once, never decompressed to disk:
#
#   .zst --tee--> shasum                      -> sha256.txt
#        --zstd -dc--tee--> dump_pass.py volume  -> volume_hourly.csv, subreddit_totals.csv
#                     \--> ripgrep prefilter --> dump_pass.py hits -> hits.csv
#
# volume: objects per UTC hour and per subreddit, all of Reddit (gap check, denominators)
# hits:   comments / submissions in ALL subreddits that mention Fedorov, kartonky
#         or protests in Cyrillic (coverage of our 8 subreddits, missed communities)
#
# Usage:    scripts/reddit/dump_pass.sh <RC_or_RS_YYYY-MM.zst> [out_root]
#           results go to <out_root>/<RC_YYYY-MM>/, out_root defaults to data/reddit_allsubs
# Requires: zstd, ripgrep, python3 (stdlib only); pv optional  ->  brew install zstd ripgrep pv
set -euo pipefail

IN="$1"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
NAME="$(basename "$IN" .zst)"
OUT="${2:-$ROOT/data/reddit_allsubs}/$NAME"
mkdir -p "$OUT"

# Loose prefilter, exact matching happens in dump_pass.py on the text fields only
PREFILTER='fedorov|федоров|фёдоров|картонк|картонн|cardboard|протест|мітинг|митинг'

# Named pipes instead of >(...): macOS ships bash 3.2, where `wait` does not
# wait for process substitutions
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkfifo "$TMP/sha" "$TMP/vol"

start=$(date +%s)
echo "start $NAME $(date '+%Y-%m-%d %H:%M:%S')" >> "$OUT/run.log"

shasum -a 256 < "$TMP/sha" | awk -v f="$(basename "$IN")" '{print $1 "  " f}' > "$OUT/sha256.txt" &
SHA_PID=$!
python3 "$HERE/dump_pass.py" volume "$OUT" < "$TMP/vol" &
VOL_PID=$!

if command -v pv >/dev/null; then
  READ=(pv -c -N "$NAME" -pterab "$IN")   # -c: several passes in parallel keep separate bars
else
  READ=(cat "$IN")
fi

"${READ[@]}" | tee "$TMP/sha" | zstd -dc --long=31 | tee "$TMP/vol" \
  | { rg -i --no-line-number "$PREFILTER" || [ $? -eq 1 ]; } \
  | python3 "$HERE/dump_pass.py" hits "$OUT"

wait "$SHA_PID"
wait "$VOL_PID"
echo "done $NAME in $(( $(date +%s) - start ))s -> $OUT" | tee -a "$OUT/run.log"
