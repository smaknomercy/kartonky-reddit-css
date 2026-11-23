"""
After dump_pass.sh: how much of the Reddit-wide discussion do our 8 subreddits
cover, and which communities did we miss?

Reads  data/reddit_allsubs/*/hits.csv and config/subreddits.txt
Writes data/reddit_allsubs/coverage_report.md (also printed)

Usage: python scripts/reddit/coverage.py [--top 25] [--dir data/reddit_allsubs]
"""
import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PASS_DIR = ROOT / "data" / "reddit_allsubs"

# (label, row filter). No thread-title measure: link_title is empty in ~89% of
# comments in the dumps, so thread_fedorov / thread_kartonky undercount heavily.
MEASURES = [
    ("Fedorov, own text", lambda d: d.fedorov == 1),
    ("Fedorov, own text, other Fedorovs excluded", lambda d: (d.fedorov == 1) & (d.fedorov_other == 0)),
    ("Fedorov, own text, Mykhailo / minister context", lambda d: (d.fedorov == 1) & (d.fedorov_target == 1)),
    ("Kartonky / cardboard protest, own text", lambda d: d.kartonky == 1),
    ("Protest words in Cyrillic", lambda d: d.protest_cyr == 1),
]


def md_table(t):
    """Markdown table without the tabulate dependency."""
    cols = [t.index.name or ""] + list(t.columns)
    rows = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    rows += ["| " + " | ".join(str(v) for v in [i, *r]) + " |" for i, r in zip(t.index, t.values)]
    return "\n".join(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--dir", type=Path, default=PASS_DIR)
    args = ap.parse_args()
    pass_dir = args.dir

    ours = set((ROOT / "config" / "subreddits.txt").read_text().split())
    files = sorted(pass_dir.glob("*/hits.csv"))
    parts = [pd.read_csv(f, dtype={"id": str}, low_memory=False).assign(dump=f.parent.name) for f in files]
    df = pd.concat([p for p in parts if len(p)], ignore_index=True)
    df["ours"] = df.subreddit.isin(ours)
    # personal profile pages (u_<name>) are one bucket, not communities
    df["subreddit"] = df.subreddit.where(~df.subreddit.str.startswith("u_", na=False), "u_* (user profiles)")

    out = [f"# Coverage of our 8 subreddits across all of Reddit\n",
           f"Dumps: {', '.join(f.parent.name for f in files)}. Rows: {len(df):,}.\n",
           "## Share inside our 8\n",
           "| Measure | Kind | All Reddit | Our 8 | Share |", "|---|---|---:|---:|---:|"]
    for label, cond in MEASURES:
        for kind in ("comment", "submission"):
            d = df[cond(df) & (df.kind == kind)]
            share = f"{d.ours.mean():.0%}" if len(d) else "–"
            out.append(f"| {label} | {kind} | {len(d):,} | {d.ours.sum():,} | {share} |")

    fed = df[df.fedorov == 1]
    by_sub = (fed.groupby("subreddit")
                 .agg(comments=("kind", lambda k: (k == "comment").sum()),
                      submissions=("kind", lambda k: (k == "submission").sum()),
                      other_fedorov=("fedorov_other", "sum"))
                 .assign(total=lambda t: t.comments + t.submissions)
                 .sort_values("total", ascending=False))
    smallest_ours = by_sub[by_sub.index.isin(ours)].total.min()

    out += ["\n## Our 8 (Fedorov in own text)\n", md_table(by_sub[by_sub.index.isin(ours)]),
            f"\n## Top {args.top} outside our 8\n",
            f"Smallest of ours: {smallest_ours:,}. Subreddits above it are worth a look "
            "(check `other_fedorov`: other Fedorovs, not the event).\n",
            md_table(by_sub[~by_sub.index.isin(ours)].head(args.top))]

    # Do our 8 follow the same timeline as the rest of Reddit? (comments, Fedorov in own text)
    c = df[(df.kind == "comment") & (df.fedorov == 1) & (df.fedorov_other == 0)].copy()
    c["day"] = (pd.to_datetime(c.created_utc.astype(int), unit="s", utc=True)
                  .dt.tz_convert("Europe/Kyiv").dt.date)
    daily = c.groupby(["day", "ours"]).size().unstack(fill_value=0).rename(columns={True: "ours", False: "other"})
    out += ["\n## Timeline: our 8 vs the rest of Reddit (comments, Fedorov in own text, Kyiv days)\n",
            f"Days: {len(daily)}. Correlation of daily counts: Pearson {daily.ours.corr(daily.other):.2f}, "
            f"Spearman {daily.ours.rank().corr(daily.other.rank()):.2f}.\n",
            md_table(pd.DataFrame({"ours": daily.ours.nlargest(5).values,
                                   "ours_day": [str(x) for x in daily.ours.nlargest(5).index],
                                   "other": daily.other.nlargest(5).values,
                                   "other_day": [str(x) for x in daily.other.nlargest(5).index]},
                                  index=pd.Index(range(1, 6), name="rank")))]

    text = "\n".join(out) + "\n"
    (pass_dir / "coverage_report.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
