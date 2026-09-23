"""
Summarize run_scan_fast.sh / run_scan.sh output and help pick subreddits.

Merges the monthly subreddit_hits_*.csv files, ranks subreddits by a chosen
keyword group, flags typical noise and writes a human-readable report.

Noise flags (heuristics, check by eye before trusting):
  bot       news-repost bots / aggregators (names ending in "auto",
            AutoNewspaper, NoFilterNews) and user profiles (u_*)
  offtopic  hits come almost only from the "protest" group with almost no
            Ukraine words (e.g. Indian protests, "Protestant" in religious subs)
  broad     Ukraine is a small share of the subreddit's hits (general sub)

Usage:
  python3 scripts/reddit/analyze_scan.py
  python3 scripts/reddit/analyze_scan.py --sort-by ukraine --top 50
  python3 scripts/reddit/analyze_scan.py --min-fedorov 10 --show-noise

Outputs (next to the input CSVs):
  scan_merged.csv   all subreddits, all months, with flags
  scan_report.md    ranked table + excluded noise, readable in any editor/GitHub
"""
import argparse
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SCAN_DIR = ROOT / "data/reddit_scan"
GROUPS = ["fedorov", "protest", "ukraine"]
BOT_RE = re.compile(r"(?:auto$|^AutoNewspaper$|^NoFilterNews$|^u_)", re.I)


def load(scan_dir):
    frames = []
    for path in sorted(scan_dir.glob("subreddit_hits_*.csv")):
        month = path.stem.replace("subreddit_hits_", "")
        # keep_default_na: subreddits literally named "null"/"NA" must stay strings
        df = pd.read_csv(path, keep_default_na=False)
        if df.empty:
            print(f"skip empty file: {path.name}")
            continue
        df["month"] = month
        frames.append(df)
    if not frames:
        raise SystemExit(f"No non-empty subreddit_hits_*.csv in {scan_dir}")
    return pd.concat(frames, ignore_index=True)


def summarize(long):
    months = sorted(long["month"].unique())
    total = long.groupby("subreddit")[["hits_total", *GROUPS]].sum()
    # per-month hits for the ranking group, to see if activity is stable
    per_month = long.pivot_table(index="subreddit", columns="month",
                                 values="hits_total", aggfunc="sum", fill_value=0)
    per_month.columns = [f"hits_{m}" for m in per_month.columns]
    df = total.join(per_month).fillna(0).astype(int)

    df["ukraine_share"] = (df["ukraine"] / df["hits_total"]).round(2)
    df["flag_bot"] = df.index.to_series().str.contains(BOT_RE)
    df["flag_offtopic"] = (df["protest"] >= 0.8 * df["hits_total"]) & (df["ukraine_share"] < 0.1)
    df["flag_broad"] = df["ukraine_share"] < 0.3
    df["flags"] = (df[["flag_bot", "flag_offtopic", "flag_broad"]]
                   .apply(lambda r: ",".join(n[5:] for n, v in r.items() if v), axis=1))
    return df, months


def to_markdown(df, cols):
    """Minimal markdown table (no tabulate dependency)."""
    head = "| subreddit | " + " | ".join(cols) + " |"
    sep = "|---|" + "---|" * len(cols)
    rows = [f"| {idx} | " + " | ".join(str(r[c]) for c in cols) + " |"
            for idx, r in df.iterrows()]
    return "\n".join([head, sep, *rows])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scan-dir", type=Path, default=SCAN_DIR)
    ap.add_argument("--sort-by", choices=["hits_total", *GROUPS], default="fedorov",
                    help="keyword group to rank by (default: fedorov, the most specific)")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--min-fedorov", type=int, default=0, help="hide subs with fewer Fedorov hits")
    ap.add_argument("--show-noise", action="store_true", help="keep bot/offtopic subs in the ranking")
    args = ap.parse_args()

    df, months = summarize(load(args.scan_dir))
    df.sort_values(args.sort_by, ascending=False).to_csv(args.scan_dir / "scan_merged.csv")

    ranked = df[df["fedorov"] >= args.min_fedorov]
    noise = ranked[ranked["flag_bot"] | ranked["flag_offtopic"]]
    if not args.show_noise:
        ranked = ranked.drop(noise.index)
    ranked = ranked.sort_values(args.sort_by, ascending=False).head(args.top)

    cols = ["hits_total", *GROUPS, "ukraine_share", *[f"hits_{m}" for m in months], "flags"]
    noise_top = noise.sort_values("hits_total", ascending=False).head(20)

    report = [
        "# Reddit subreddit scan report",
        "",
        f"Months: {', '.join(months)} | subreddits with any hit: {len(df):,} | "
        f"ranked by `{args.sort_by}`",
        "",
        f"## Top {len(ranked)} candidates" + (" (noise hidden)" if not args.show_noise else ""),
        "",
        to_markdown(ranked, cols),
        "",
        "## Excluded as noise (top 20 by volume)",
        "",
        to_markdown(noise_top, ["hits_total", *GROUPS, "ukraine_share", "flags"]),
        "",
        "Flags: `bot` = repost bot / aggregator / user profile; "
        "`offtopic` = protest words without Ukraine words; "
        "`broad` = Ukraine < 30% of the sub's hits (general sub, still may be useful).",
    ]
    out = args.scan_dir / "scan_report.md"
    out.write_text("\n".join(report) + "\n", encoding="utf-8")

    pd.set_option("display.width", 200)
    print(ranked[cols].to_string())
    print(f"\nnoise excluded: {len(noise)} subs | saved {out.relative_to(ROOT)} "
          f"and scan_merged.csv")


if __name__ == "__main__":
    main()
