#!/usr/bin/env bash
# Extra-months pass: extract the 8 subreddits from the monthly dumps, clean them,
# and checksum the .zst files (for the methodology) before deletion.
# Writes to separate folders (data/reddit_raw_<tag>, data/clean_<tag>), so the Jul-Aug data stays untouched.
#
# Arguments: month regex for the dump file names, folder tag. Defaults: Mar-Jun baseline.
# Run on the Mac from the repo root:
#   caffeinate -is scripts/reddit/baseline_pass.sh                    # 0[3-6] -> *_baseline
#   caffeinate -is scripts/reddit/baseline_pass.sh "0[1-2]" janfeb    # Jan-Feb -> *_janfeb
# Expected for 4 months: submissions ~15 min, comments ~1 h, cleaning a few minutes.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
ROOT="$(dirname "$REPO")"
EXTRACTOR="$ROOT/tools/reddit-dump-extractor"
MONTHS="${1:-0[3-6]}"
TAG="${2:-baseline}"
RAW="$ROOT/data/reddit_raw_$TAG"
CLEAN="$ROOT/data/clean_$TAG"
SUBS="$(paste -sd, "$REPO/config/subreddits.txt")"
FIELDS="$(paste -sd, "$REPO/config/comment_fields.txt")"

mkdir -p "$RAW"
exec > >(tee -a "$RAW/run.log") 2>&1
echo "== start $(date '+%F %T') | months $MONTHS -> $TAG"

# Checksums run alongside extraction: hashing is disk-bound, extraction CPU-bound.
(cd "$REPO" && shasum -a 256 reddit_$MONTHS/*/*.zst > "$RAW/sha256.txt" \
  && echo "== sha256 done $(date '+%F %T')") &
SHA_PID=$!

cd "$EXTRACTOR"
venv/bin/python reddit_zst_filter_zstandard.py "$REPO" --file_filter "^RS_2026-$MONTHS" \
  --value "$SUBS" --output_dir "$RAW" --workers 2
echo "== submissions done $(date '+%F %T')"
venv/bin/python reddit_zst_filter_zstandard.py "$REPO" --file_filter "^RC_2026-$MONTHS" \
  --value "$SUBS" --fields "$FIELDS" --output_dir "$RAW" --workers 2
echo "== comments done $(date '+%F %T')"

cd "$REPO"
.venv/bin/python scripts/reddit/clean_reddit.py --raw-dir "$RAW" --out-dir "$CLEAN"

wait "$SHA_PID"
echo "== all done $(date '+%F %T')"
ls -la "$RAW" "$CLEAN"
