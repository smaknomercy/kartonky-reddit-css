"""
Step 3a of the pipeline: find which subreddits discuss the July 2026
protests, before extracting anything.

Reads pre-filtered submission JSON lines from stdin and counts keyword hits
per subreddit (exact regex check on title + selftext). Output: CSV sorted by
hit count. Normally called by run_scan.sh:

  zstd -dc --long=31 RS_2026-07.zst | rg -i "$PATTERN" \
    | python3 scan_subreddits.py > subreddit_hits_2026-07.csv
"""
import csv
import json
import re
import sys
from collections import Counter, defaultdict

# Keyword groups; a submission can hit several groups.
GROUPS = {
    "fedorov": r"fedorov|федоров",
    "protest": r"protest|rally|демонстрац|протест|мітинг|митинг|картонк|cardboard",
    "ukraine": r"ukrain|україн|украин|kyiv|kiev|київ|киев|zelensk|зеленськ",
}
GROUP_RE = {k: re.compile(v, re.I) for k, v in GROUPS.items()}

total = Counter()
by_group = defaultdict(Counter)
bad_lines = 0

for line in sys.stdin:
    try:
        post = json.loads(line)
    except json.JSONDecodeError:
        bad_lines += 1
        continue
    sub = post.get("subreddit") or ""
    text = f"{post.get('title', '')} {post.get('selftext', '')}"
    hit_any = False
    for name, rx in GROUP_RE.items():
        if rx.search(text):
            by_group[name][sub] += 1
            hit_any = True
    if hit_any:
        total[sub] += 1

writer = csv.writer(sys.stdout)
writer.writerow(["subreddit", "hits_total", *GROUPS.keys()])
for sub, n in total.most_common():
    writer.writerow([sub, n, *(by_group[g][sub] for g in GROUPS)])
print(f"bad_lines={bad_lines}", file=sys.stderr)
