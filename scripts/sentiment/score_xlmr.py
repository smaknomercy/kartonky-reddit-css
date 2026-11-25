"""
Sentiment scores with cardiffnlp/twitter-xlm-roberta-base-sentiment (same model as HW3) for H2 and validation.

Units scored
  daysample : up to K comments per subreddit per Kyiv day, Mar-Aug 2026 (not removed, not AutoModerator).
              Equal draw per subreddit-day, so shifts in a group's subreddit mix (r/worldnews in Mar-Apr)
              do not leak into the group's tone. Selection is a seeded hash of the comment id: reproducible.
  fedorov   : every comment of the LLM pool (comment_pool(): Jul-Aug, Fedorov in body or thread title)
              plus Mar-Jun comments that mention Fedorov in the body.
  poster    : every unique poster text (by text_norm), incl. the 300 of the manual annotation sample.
A comment can be in both daysample and fedorov; it is scored once.

Output: data/sentiment/xlmr_v1/
  strata.csv   eligible comments per subreddit-day (weights for volume-weighted estimates)
  units.csv    what to score (id, source, subreddit, date_local, flags), built once
  scores.csv   p_neg, p_neu, p_pos per unit; appended in batches, so an interrupted run resumes

Usage (on the Mac, needs: pip install torch transformers sentencepiece)
  python scripts/sentiment/score_xlmr.py --k 60
"""
import argparse
import re
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "labeling"))
from make_annotation_sample import FEDOROV, comment_pool  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
COMMENT_FILES = [DATA / "clean_baseline" / "reddit_comments.csv", DATA / "clean" / "reddit_comments.csv"]
OUT = DATA / "sentiment" / "xlmr_v1"
MODEL = "cardiffnlp/twitter-xlm-roberta-base-sentiment"
# the model may report either names or LABEL_i
LABELS = {"negative": "p_neg", "neutral": "p_neu", "positive": "p_pos",
          "label_0": "p_neg", "label_1": "p_neu", "label_2": "p_pos"}
URL_RE = re.compile(r"https?://\S+|www\.\S+")
HASH_KEY = "kartonky-h4-2026"  # 16 chars, fixes the random draw


def clean_for_model(t):
    """Same preprocessing as HW3: drop URLs and quoted lines, collapse spaces, cap length."""
    t = URL_RE.sub("", str(t))
    t = re.sub(r"^&gt;.*$", "", t, flags=re.M)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:1000] if t else "."


def build_units(k):
    cols = ["id", "subreddit", "date_local", "body", "text_removed", "is_automod"]
    cand, fed, strata = [], [], []
    for f in COMMENT_FILES:
        for ch in pd.read_csv(f, usecols=cols, dtype=str, keep_default_na=False, chunksize=300_000):
            ch = ch[(ch.text_removed != "True") & (ch.is_automod != "True") & (ch.body.str.strip() != "")]
            strata.append(ch.groupby(["subreddit", "date_local"]).size().rename("n_eligible"))
            ch = ch.assign(h=pd.util.hash_pandas_object(ch.id, index=False, hash_key=HASH_KEY).values)
            cand.append(ch[["id", "subreddit", "date_local", "h"]])
            # keep only the k smallest hashes per subreddit-day seen so far
            c = pd.concat(cand)
            cand = [c.sort_values("h").groupby(["subreddit", "date_local"]).head(k)]
            fed.append(ch.loc[ch.body.str.contains(FEDOROV, case=False, regex=True), ["id", "subreddit", "date_local"]])
        print(f"  scanned {f.parent.name}/{f.name}", flush=True)
    strata = pd.concat(strata).groupby(level=[0, 1]).sum().reset_index()
    day = cand[0].drop(columns="h").assign(daysample=True)
    # date_local too: without it the pool comments had no date in units.csv (see unit_meta.py)
    pool = comment_pool([])[["id", "subreddit", "date_local"]].assign(fedorov=True)
    fed = pd.concat(fed).assign(fedorov=True)
    fed = pd.concat([fed, pool]).drop_duplicates("id")
    com = day.merge(fed, on="id", how="outer", suffixes=("", "_f"))
    for c in ["subreddit", "date_local"]:
        if f"{c}_f" in com:
            com[c] = com[c].fillna(com[f"{c}_f"])
    com = com[["id", "subreddit", "date_local", "daysample", "fedorov"]].assign(source="comment")
    com[["daysample", "fedorov"]] = com[["daysample", "fedorov"]].astype("boolean").fillna(False).astype(bool)

    k_ = pd.read_csv(DATA / "clean" / "kartonky_clean.csv")
    k_ = k_[k_.has_text & k_.text.notna()].drop_duplicates("text_norm")
    post = pd.DataFrame({"id": k_.id.astype(str), "subreddit": "", "date_local": k_.date_local.fillna(""),
                         "daysample": False, "fedorov": False, "source": "poster"})
    return pd.concat([com, post], ignore_index=True), strata


def load_texts(units):
    """Bodies for the comment ids, texts for posters; one more pass over the comment files."""
    want = set(units.loc[units.source == "comment", "id"])
    texts = {}
    for f in COMMENT_FILES:
        for ch in pd.read_csv(f, usecols=["id", "body"], dtype=str, keep_default_na=False, chunksize=300_000):
            ch = ch[ch.id.isin(want)]
            texts.update(zip(ch.id, ch.body))
    k_ = pd.read_csv(DATA / "clean" / "kartonky_clean.csv", usecols=["id", "text"])
    texts.update(zip(k_.id.astype(str), k_.text.astype(str)))
    return units.id.map(texts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=60, help="comments per subreddit per day in the day sample")
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--units-only", action="store_true", help="build units.csv and strata.csv, no scoring")
    ap.add_argument("--flush-every", type=int, default=2000, help="units per write to scores.csv")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    units_path = OUT / "units.csv"
    if units_path.exists():
        units = pd.read_csv(units_path, dtype={"id": str, "subreddit": str, "date_local": str}, keep_default_na=False)
        print(f"units: {len(units):,} (from {units_path.name})")
    else:
        print("Building units ...", flush=True)
        units, strata = build_units(args.k)
        strata.to_csv(OUT / "strata.csv", index=False)
        units.to_csv(units_path, index=False)
        print(f"units: {len(units):,} | daysample {units.daysample.sum():,}, fedorov {units.fedorov.sum():,}, "
              f"posters {(units.source == 'poster').sum():,}")

    if args.units_only:
        return
    scores_path = OUT / "scores.csv"
    done = set(pd.read_csv(scores_path, usecols=["id", "source"], dtype=str).apply(tuple, axis=1)) \
        if scores_path.exists() else set()
    todo = units[~units[["id", "source"]].apply(tuple, axis=1).isin(done)].copy()
    print(f"to score: {len(todo):,} (already done {len(done):,})")
    if todo.empty:
        return
    todo["text"] = load_texts(todo).fillna("").map(clean_for_model)

    import torch
    from transformers import pipeline
    device = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
    clf = pipeline("text-classification", model=MODEL, top_k=None, truncation=True, max_length=200, device=device)
    print(f"model {MODEL} on {device}", flush=True)

    start, n = time.time(), 0
    for i in range(0, len(todo), args.flush_every):
        part = todo.iloc[i:i + args.flush_every]
        res = clf(part.text.tolist(), batch_size=args.batch)
        probs = pd.DataFrame([{LABELS[x["label"].lower()]: x["score"] for x in r} for r in res], index=part.index)
        out = pd.concat([part[["id", "source"]], probs[["p_neg", "p_neu", "p_pos"]].round(4)], axis=1)
        out.to_csv(scores_path, mode="a", header=not scores_path.exists(), index=False)
        n += len(part)
        el = time.time() - start
        print(f"{n:,}/{len(todo):,} | {n / el:.0f}/s | ETA {(len(todo) - n) / (n / el) / 60:.1f} min", flush=True)
    print(f"Saved to {scores_path}")


if __name__ == "__main__":
    main()
