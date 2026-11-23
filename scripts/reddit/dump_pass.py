"""
Consumers for dump_pass.sh. Both modes read decompressed dump lines from stdin.

  volume <out_dir>  every line: counts per UTC hour and per subreddit
                    -> volume_hourly.csv (hour_utc, n), subreddit_totals.csv (subreddit, n)
  hits <out_dir>    ripgrep-prefiltered lines: exact keyword match on the text fields,
                    one row per matching comment / submission -> hits.csv

A row is kept in hits.csv if its own text (comment body, or submission title +
selftext) matches a keyword group, or if it is a comment under a thread whose
title mentions Fedorov / kartonky. Which Fedorov is meant is only flagged
(fedorov_target / fedorov_other), the analysis decides what to keep.
"""
import csv
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

GROUPS = {
    # not the villages Fedorovka / Федоровка, not patronymics Fedorovich / Федорович
    "fedorov": r"fedorov(?!k|i?ch|ych)|ф[её]доров(?!к|ич)",
    "kartonky": r"картонк|картонн\w*\s+(?:протест|плакат|табличк)",
    # not протестант(ський) / протестировать (ru: to test)
    "protest_cyr": r"протест(?!ант|ир)|мітинг|митинг",
}
# "cardboard sign" alone is mostly panhandling or memes: it counts as kartonky
# only with protest / Ukraine context in the same text or thread title
CARDBOARD = r"cardboard[\s-]+(?:signs?|protests?|posters?|placards?)"
CARDBOARD_CONTEXT = (r"protest|rally|demonstrat|ukrain|kyiv|kiev|zelensk|fedorov"
                     r"|україн|київ|протест")
THREAD_GROUPS = ("fedorov", "kartonky")

# Mykhailo Fedorov (the event) vs other Fedorovs (Zaporizhzhia's Ivan Fedorov, NHL's Sergei Fedorov)
FEDOROV_TARGET = (r"myk?ha[iy]lo|mikhail|михайл|михаил|mintsyfr|мінцифр|минцифр"
                  r"|digital (?:minister|transformation)|defen[cs]e minister|minist(?:er|ry) of defen"
                  r"|міністр оборони|министр обороны|міноборон|минобороны")
FEDOROV_OTHER = (r"ivan fedorov|іван федоров|иван федоров|zaporizh|запоріж|запорож"
                 r"|\bnhl\b|hockey|red wings|kraken|serg[eu][iy] fedorov|сергій федоров|сергей федоров"
                 r"|avtomat|автомат федорова")

FIELDS = ["kind", "id", "subreddit", "created_utc", "author", "score", "link_id", "parent_id",
          "num_comments", "thread_title", "text", "permalink", "url",
          *GROUPS, *(f"thread_{g}" for g in THREAD_GROUPS), "fedorov_target", "fedorov_other"]


def log(out_dir, msg):
    with open(out_dir / "run.log", "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def run_volume(out_dir):
    k_ts, k_sub = b'"created_utc":', b'"subreddit":"'
    hours, subs = Counter(), Counter()
    n = bad = 0
    t0 = time.time()
    for line in sys.stdin.buffer:
        n += 1
        i = line.find(k_ts)        # first: top-level created_utc precedes crosspost_parent_list (keys are sorted)
        j = line.rfind(k_sub)      # last: crossposted parents (nested, same keys) come before top-level subreddit
        if i < 0 or j < 0:
            bad += 1
            continue
        i += len(k_ts)
        try:
            ts = int(float(line[i:line.find(b",", i)].strip(b'" ')))
        except ValueError:
            bad += 1
            continue
        hours[ts // 3600] += 1
        j += len(k_sub)
        subs[line[j:line.find(b'"', j)]] += 1

    with open(out_dir / "volume_hourly.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["hour_utc", "n"])
        for h in sorted(hours):
            w.writerow([time.strftime("%Y-%m-%d %H:00", time.gmtime(h * 3600)), hours[h]])
    with open(out_dir / "subreddit_totals.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["subreddit", "n"])
        for s, c in subs.most_common():
            w.writerow([s.decode("utf-8", "replace"), c])
    log(out_dir, f"[volume] lines {n:,}, unparsed {bad:,}, hours {len(hours)}, "
                 f"subreddits {len(subs):,}, {time.time() - t0:.0f}s")


def run_hits(out_dir):
    rx = {g: re.compile(p, re.I) for g, p in GROUPS.items()}
    rx_target, rx_other = re.compile(FEDOROV_TARGET, re.I), re.compile(FEDOROV_OTHER, re.I)
    rx_card, rx_card_ctx = re.compile(CARDBOARD, re.I), re.compile(CARDBOARD_CONTEXT, re.I)

    def kartonky(text, context):
        return bool(rx["kartonky"].search(text)) or (
            bool(rx_card.search(text)) and bool(rx_card_ctx.search(context)))

    n_in = n_out = bad = 0
    counts = Counter()
    with open(out_dir / "hits.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for line in sys.stdin.buffer:
            n_in += 1
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            is_comment = "body" in o
            if is_comment:
                own, thread = o.get("body") or "", o.get("link_title") or ""
            else:
                thread = o.get("title") or ""
                own = f"{thread}\n{o.get('selftext') or ''}"
            both = f"{own}\n{thread}"
            own_hit = {g: bool(r.search(own)) for g, r in rx.items()}
            own_hit["kartonky"] = kartonky(own, both)
            thread_hit = {"fedorov": bool(rx["fedorov"].search(thread)),
                          "kartonky": kartonky(thread, thread)}
            if not (any(own_hit.values()) or any(thread_hit.values())):
                continue
            row = {
                "kind": "comment" if is_comment else "submission",
                "id": o.get("id"), "subreddit": o.get("subreddit"),
                "created_utc": o.get("created_utc"), "author": o.get("author"),
                "score": o.get("score"), "link_id": o.get("link_id"),
                "parent_id": o.get("parent_id"), "num_comments": o.get("num_comments"),
                "thread_title": thread, "text": own,
                "permalink": o.get("permalink"), "url": None if is_comment else o.get("url"),
                **own_hit, **{f"thread_{g}": v for g, v in thread_hit.items()},
                "fedorov_target": bool(rx_target.search(both)),
                "fedorov_other": bool(rx_other.search(both)),
            }
            w.writerow({k: int(v) if isinstance(v, bool) else v for k, v in row.items()})
            n_out += 1
            counts.update(k for k, v in row.items() if v is True)
    log(out_dir, f"[hits] prefiltered lines {n_in:,}, kept {n_out:,}, unparsed {bad:,}; "
                 + ", ".join(f"{k} {v:,}" for k, v in counts.most_common()))


if __name__ == "__main__":
    mode, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    {"volume": run_volume, "hits": run_hits}[mode](out)
