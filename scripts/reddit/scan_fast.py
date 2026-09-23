"""
Fast subreddit scan: replaces `grep -iE` in the pipeline (BSD grep on macOS is
very slow with case-insensitive Cyrillic regexes).

Reads raw JSON lines from stdin (zstd -dc ... |), pre-filters on bytes,
parses only candidate lines and counts keyword hits per subreddit.
Prints progress to stderr every 2M lines.

Usage:
  zstd -dc --long=31 RS_2026-07.zst | python3 scan_fast.py > hits.csv
"""
import csv
import json
import re
import sys
import time
from collections import Counter, defaultdict

ASCII_KEYS = [b"fedorov", b"protest", b"cardboard", b"ukrain",
              b"kyiv", b"kiev", b"zelensk"]
CYR_STEMS = ["федоров", "протест", "мітинг", "картонк", "україн", "украин",
             "київ", "зеленськ"]
# One compiled bytes regex as the pre-filter (runs in C, much faster than
# Python loops). re.I on bytes folds ASCII only, so Cyrillic is listed in
# the three usual casings.
CYR_KEYS = sorted({v.encode() for s in CYR_STEMS
                   for v in (s, s.capitalize(), s.upper())})
PREFILTER = re.compile(b"|".join([re.escape(k) for k in ASCII_KEYS + CYR_KEYS]),
                       re.I)

GROUPS = {
    "fedorov": re.compile(r"fedorov|федоров", re.I),
    "protest": re.compile(r"protest|\\brally\\b|демонстрац|протест|мітинг|митинг|картонк|cardboard", re.I),
    "ukraine": re.compile(r"ukrain|україн|украин|kyiv|kiev|київ|киев|zelensk|зеленськ", re.I),
}

total = Counter()
by_group = defaultdict(Counter)
n_lines = n_cand = 0
t0 = time.time()

for line in sys.stdin.buffer:
    n_lines += 1
    if n_lines % 2_000_000 == 0:
        print(f"{n_lines/1e6:.0f}M lines, {n_cand} candidates, "
              f"{time.time()-t0:.0f}s", file=sys.stderr, flush=True)
    if not PREFILTER.search(line):
        continue
    n_cand += 1
    try:
        post = json.loads(line)
    except json.JSONDecodeError:
        continue
    text = f"{post.get('title') or ''} {post.get('selftext') or ''}"
    sub = post.get("subreddit") or ""
    hit = False
    for name, rx in GROUPS.items():
        if rx.search(text):
            by_group[name][sub] += 1
            hit = True
    if hit:
        total[sub] += 1

w = csv.writer(sys.stdout)
w.writerow(["subreddit", "hits_total", *GROUPS])
for sub, n in total.most_common():
    w.writerow([sub, n, *(by_group[g][sub] for g in GROUPS)])
print(f"done: {n_lines} lines, {n_cand} candidates, {len(total)} subreddits, "
      f"{time.time()-t0:.0f}s", file=sys.stderr)
