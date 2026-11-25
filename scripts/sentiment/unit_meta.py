"""
Metadata for the scored comments: thread, Kyiv date and how Fedorov is mentioned.

Why: units.csv has no date for ~11.7k Fedorov-pool comments (comment_pool() returns id and subreddit
only), and the H2 gap analysis needs the thread (link_id) to bootstrap by thread, since comments
in one thread are not independent.

Output: data/sentiment/xlmr_v1/unit_meta.csv
  id, link_id, date_local, in_body, in_title
  in_body : Fedorov in the comment body (FEDOROV regex, same as everywhere)
  in_title: Fedorov in the thread title (link_title, or the submission title when it is empty)

Usage: python scripts/sentiment/unit_meta.py            # both comment files
       python scripts/sentiment/unit_meta.py --part 0   # one file (keeps a run short)
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "labeling"))
from make_annotation_sample import FEDOROV  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
SDIR = DATA / "sentiment" / "xlmr_v1"
PARTS = [DATA / "clean_baseline", DATA / "clean"]


def scan(folder, want):
    subs = pd.read_csv(folder / "reddit_submissions.csv", usecols=["id", "title"], dtype=str, keep_default_na=False)
    titles = dict(zip("t3_" + subs.id, subs.title))
    cols = ["id", "link_id", "body", "link_title", "date_local"]
    out = []
    for ch in pd.read_csv(folder / "reddit_comments.csv", usecols=cols, dtype=str, keep_default_na=False,
                          chunksize=300_000):
        ch = ch[ch.id.isin(want)]
        if ch.empty:
            continue
        title = ch.link_title.where(ch.link_title != "", ch.link_id.map(titles)).fillna("")
        out.append(pd.DataFrame({"id": ch.id, "link_id": ch.link_id, "date_local": ch.date_local,
                                 "in_body": ch.body.str.contains(FEDOROV, case=False, regex=True),
                                 "in_title": title.str.contains(FEDOROV, case=False, regex=True)}))
    return pd.concat(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", type=int, choices=range(len(PARTS)))
    args = ap.parse_args()
    u = pd.read_csv(SDIR / "units.csv", dtype=str, keep_default_na=False)
    want = set(u.loc[u.source == "comment", "id"])
    parts = [args.part] if args.part is not None else range(len(PARTS))
    for i in parts:
        m = scan(PARTS[i], want)
        m.to_csv(SDIR / f"unit_meta_part{i}.csv", index=False)
        print(f"{PARTS[i].name}: {len(m):,} comments", flush=True)
    files = [SDIR / f"unit_meta_part{i}.csv" for i in range(len(PARTS))]
    if all(f.exists() for f in files):
        m = pd.concat(pd.read_csv(f, dtype=str, keep_default_na=False) for f in files).drop_duplicates("id")
        m.to_csv(SDIR / "unit_meta.csv", index=False)
        miss = len(want - set(m.id))
        print(f"unit_meta.csv: {len(m):,} of {len(want):,} scored comments; missing {miss}")


if __name__ == "__main__":
    main()
