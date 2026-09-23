"""
Clean the Reddit extracts for analysis (H2, Task 1).

Input : data/reddit_raw/RS_*.csv (submissions), RC_*.csv (comments)
        = output of tools/reddit-dump-extractor for the 8 selected subreddits
Output: data/clean/reddit_submissions.csv
        data/clean/reddit_comments.csv
        data/clean/reddit_cleaning_log.txt   (every step + counts, for the report)

What it does
 1. Reads everything as text; the extractor writes Python-style "None"/"nan"
    for missing values -> turned into empty cells.
 2. Keeps the columns we analyse (the raw files keep all ~80-120 fields).
 3. Drops duplicate ids (across months too).
 4. Adds UTC and Kyiv-local datetime + date (same day definition as Kartonky).
 5. Flags, does not drop: removed/deleted text, deleted author, AutoModerator,
    top-level comments. Removed posts still show *that* something was said
    and when, which matters for activity-over-time.

Comments are processed in chunks, so memory stays flat on ~1 GB inputs.

Usage: python scripts/reddit/clean_reddit.py [--raw-dir DIR] [--out-dir DIR]
"""
import argparse
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
KYIV_TZ = "Europe/Kyiv"
MISSING = {"None", "nan", "NaN"}
REMOVED_TEXT = {"[removed]", "[deleted]"}
CHUNK = 200_000

SUB_COLS = ["id", "subreddit", "author", "created_utc", "title", "selftext",
            "url", "domain", "score", "upvote_ratio", "num_comments",
            "link_flair_text", "is_self", "over_18", "removed_by_category", "permalink"]
COM_COLS = ["id", "link_id", "parent_id", "subreddit", "author", "author_flair_text",
            "created_utc", "body", "score", "controversiality", "is_submitter",
            "edited", "link_title", "permalink"]


def add_time(df):
    ts = pd.to_datetime(pd.to_numeric(df["created_utc"], errors="coerce"), unit="s", utc=True)
    local = ts.dt.tz_convert(KYIV_TZ)
    df["datetime_utc"] = ts.dt.strftime("%Y-%m-%d %H:%M:%S")
    df["datetime_local"] = local.dt.strftime("%Y-%m-%d %H:%M:%S")
    df["date_local"] = local.dt.strftime("%Y-%m-%d")
    return df


def clean_chunk(df, cols, text_col, kind):
    df = df[[c for c in cols if c in df.columns]].copy()
    for c in cols:  # a month's file may lack a rare field
        if c not in df.columns:
            df[c] = ""
    df = df[cols]
    df = df.mask(df.isin(MISSING), "")
    df[text_col] = df[text_col].str.replace("\r", " ", regex=False).str.strip()
    df = add_time(df)
    df["text_removed"] = df[text_col].isin(REMOVED_TEXT)
    if kind == "submissions":
        df["text_removed"] |= df["removed_by_category"] != ""
        df["has_selftext"] = (df["selftext"] != "") & ~df["text_removed"]
    else:
        df["is_top_level"] = df["parent_id"].str.startswith("t3_")
    df["author_deleted"] = df["author"] == "[deleted]"
    df["is_automod"] = df["author"] == "AutoModerator"
    return df


def process(kind, files, cols, text_col, out_path, log):
    seen, stats = set(), Counter()
    per_sub, per_day = Counter(), Counter()
    first = True
    for path in files:
        n_file = 0
        for chunk in pd.read_csv(path, dtype=str, keep_default_na=False,
                                 chunksize=CHUNK, on_bad_lines="warn"):
            stats["rows_in"] += len(chunk)
            n_file += len(chunk)
            chunk = clean_chunk(chunk, cols, text_col, kind)
            dup = chunk["id"].isin(seen) | chunk["id"].duplicated()
            stats["duplicate_id"] += int(dup.sum())
            chunk = chunk[~dup]
            seen.update(chunk["id"])
            for flag in ["text_removed", "author_deleted", "is_automod"]:
                stats[flag] += int(chunk[flag].sum())
            per_sub.update(chunk["subreddit"])
            per_day.update(chunk["date_local"])
            chunk.to_csv(out_path, mode="w" if first else "a", header=first, index=False)
            first = False
            stats["rows_out"] += len(chunk)
        log.append(f"[{kind}] {path.name}: {n_file:,} rows")
    if first:
        log.append(f"[{kind}] no input files")
        return
    size = out_path.stat().st_size / 1e6
    days = sorted(per_day)
    log.append(f"[{kind}] rows in {stats['rows_in']:,} -> out {stats['rows_out']:,} "
               f"(duplicate ids dropped: {stats['duplicate_id']:,})")
    log.append(f"[{kind}] flagged: removed/deleted text {stats['text_removed']:,} "
               f"({stats['text_removed'] / stats['rows_out']:.1%}), deleted author "
               f"{stats['author_deleted']:,}, AutoModerator {stats['is_automod']:,}")
    log.append(f"[{kind}] local dates {days[0]} .. {days[-1]}")
    log.append(f"[{kind}] saved {out_path.relative_to(ROOT) if out_path.is_relative_to(ROOT) else out_path} "
               f"({size:.1f} MB, {len(cols) + 7} columns)")
    for s, n in per_sub.most_common():
        log.append(f"[{kind}]   r/{s}: {n:,}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", type=Path, default=ROOT / "data/reddit_raw")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "data/clean")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    log = [f"raw dir: {args.raw_dir}"]
    raw_mb = sum(p.stat().st_size for p in args.raw_dir.glob("R[SC]_*.csv")) / 1e6
    log.append(f"raw filtered size (all fields): {raw_mb:,.1f} MB")
    process("submissions", sorted(args.raw_dir.glob("RS_*.csv")), SUB_COLS, "selftext",
            args.out_dir / "reddit_submissions.csv", log)
    process("comments", sorted(args.raw_dir.glob("RC_*.csv")), COM_COLS, "body",
            args.out_dir / "reddit_comments.csv", log)
    (args.out_dir / "reddit_cleaning_log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
    print("\n".join(log))


if __name__ == "__main__":
    main()
