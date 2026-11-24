"""
H2 (reformulated for HW4): did Reddit get more negative during the protests, relative to each
group's own baseline, and do the groups differ in that change? Difference-in-differences with
day-level bootstrap intervals, plus a placebo check against ordinary weeks of the baseline.

Input : data/sentiment/xlmr_v1/{units,scores,strata}.csv (scripts/sentiment/score_xlmr.py)
        Only the day sample is used: up to K comments per subreddit per day.
Measure: share of comments whose most likely class is negative (robustness: mean p_neg).
Weights: group = mean of its subreddits (equal weights), so a change in the subreddit mix
         (r/worldnews in Mar-Apr) does not move the group; robustness: weights by volume.
Periods: baseline 02.03-30.06 (01.03 is a partial day), window 15-20.07 (as in HW3),
         plus 21.07-31.07 and August to see whether the shift lasts.
CI     : 95% percentile bootstrap over days; dates are resampled jointly for all groups,
         so the groups' shared day-to-day noise is kept in the differences.
Placebo: the same change for every 6-day window inside the baseline (vs the rest of the
         baseline); the protest window is compared with that distribution.

Output: data/sentiment/xlmr_v1/h2/
  sub_day.csv, group_day.csv  daily series (for the charts)
  periods.csv                 level per group, period and weighting, with CI
  changes.csv                 change vs baseline per group, and DiD between groups, with CI
  placebo.csv                 placebo changes per group
  h2_report.txt               the numbers in words

Usage: python scripts/sentiment/h2_did.py [--b 5000]
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SDIR = ROOT / "data" / "sentiment" / "xlmr_v1"
GROUP = {"reddit_ukr": "ua", "RedditUATalks": "ua", "Kyiv": "ua",
         "ukraine": "ua_en", "UkrainianConflict": "ua_en",
         "worldnews": "intl", "europe": "intl", "UkraineRussiaReport": "intl"}
GROUPS = ["ua", "ua_en", "intl"]
PERIODS = {"baseline": ("2026-03-02", "2026-06-30"), "window": ("2026-07-15", "2026-07-20"),
           "late_july": ("2026-07-21", "2026-07-31"), "august": ("2026-08-01", "2026-08-31")}
MEASURES = ["neg_share", "p_neg"]
WEIGHTS = ["equal", "volume"]


def load(scores_path):
    u = pd.read_csv(SDIR / "units.csv", dtype={"id": str, "subreddit": str, "date_local": str}, keep_default_na=False)
    s = pd.read_csv(scores_path, dtype={"id": str, "source": str})
    d = u[(u.source == "comment") & (u.daysample.astype(str) == "True")].merge(s, on=["id", "source"])
    d["neg_share"] = (d[["p_neg", "p_neu", "p_pos"]].idxmax(axis=1) == "p_neg").astype(float)
    d["group"] = d.subreddit.map(GROUP)
    sub_day = d.groupby(["group", "subreddit", "date_local"], as_index=False).agg(
        n=("id", "size"), neg_share=("neg_share", "mean"), p_neg=("p_neg", "mean"))
    strata = pd.read_csv(SDIR / "strata.csv", dtype={"date_local": str})
    sub_day = sub_day.merge(strata, on=["subreddit", "date_local"], how="left")
    lo, hi = PERIODS["baseline"][0], PERIODS["august"][1]
    return sub_day[(sub_day.date_local >= lo) & (sub_day.date_local <= hi)]


def group_day(sub_day):
    rows = []
    for (g, day), x in sub_day.groupby(["group", "date_local"]):
        r = {"group": g, "date_local": day, "n": x.n.sum()}
        for m in MEASURES:
            r[f"{m}_equal"] = x[m].mean()
            r[f"{m}_volume"] = np.average(x[m], weights=x.n_eligible)
        rows.append(r)
    return pd.DataFrame(rows)


def period_of(dates):
    out = pd.Series("", index=dates.index)
    for p, (lo, hi) in PERIODS.items():
        out[(dates >= lo) & (dates <= hi)] = p
    return out


def levels(gd_wide, day_period):
    """Mean over days of each period -> {(group, col, period): value}; gd_wide: index date, columns (group, col)."""
    return gd_wide.groupby(day_period).mean()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--b", type=int, default=5000, help="bootstrap replicates")
    ap.add_argument("--scores", type=Path, default=SDIR / "scores.csv")
    ap.add_argument("--out", type=Path, default=SDIR / "h2")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(2026)

    sd = load(args.scores)
    gd = group_day(sd)
    sd.to_csv(args.out / "sub_day.csv", index=False)
    gd.to_csv(args.out / "group_day.csv", index=False)

    cols = [f"{m}_{w}" for m in MEASURES for w in WEIGHTS]
    wide = gd.pivot(index="date_local", columns="group", values=cols)  # columns: (col, group)
    per = period_of(pd.Series(wide.index, index=wide.index))
    wide, per = wide[per != ""], per[per != ""]
    point = wide.groupby(per).mean()

    # joint day bootstrap: resample dates within each period, the same dates for all groups
    idx = {p: np.flatnonzero(per.values == p) for p in PERIODS}
    vals = wide.values
    boot = np.empty((args.b, len(PERIODS), vals.shape[1]))
    for b in range(args.b):
        for j, p in enumerate(PERIODS):
            pick = rng.choice(idx[p], size=len(idx[p]), replace=True)
            boot[b, j] = np.nanmean(vals[pick], axis=0)
    pnames = list(PERIODS)
    colpos = {c: i for i, c in enumerate(wide.columns)}

    def ci(a):
        return np.nanpercentile(a, [2.5, 97.5])

    lv, ch = [], []
    for c in cols:
        for g in GROUPS:
            k = colpos[(c, g)]
            for j, p in enumerate(pnames):
                lo, hi = ci(boot[:, j, k])
                lv.append({"measure": c, "group": g, "period": p, "n_days": len(idx[p]),
                           "value": point.loc[p, (c, g)], "ci_lo": lo, "ci_hi": hi})
            for j, p in enumerate(pnames[1:], start=1):
                d = boot[:, j, k] - boot[:, 0, k]
                lo, hi = ci(d)
                ch.append({"measure": c, "contrast": f"{g}: {p} - baseline",
                           "value": point.loc[p, (c, g)] - point.loc["baseline", (c, g)], "ci_lo": lo, "ci_hi": hi})
        for j, p in enumerate(pnames[1:], start=1):
            for a, bb in [("ua", "intl"), ("ua", "ua_en"), ("ua_en", "intl")]:
                ka, kb = colpos[(c, a)], colpos[(c, bb)]
                d = (boot[:, j, ka] - boot[:, 0, ka]) - (boot[:, j, kb] - boot[:, 0, kb])
                pv = (point.loc[p, (c, a)] - point.loc["baseline", (c, a)]) - \
                     (point.loc[p, (c, bb)] - point.loc["baseline", (c, bb)])
                lo, hi = ci(d)
                ch.append({"measure": c, "contrast": f"DiD {p}: {a} - {bb}", "value": pv, "ci_lo": lo, "ci_hi": hi})
    lv, ch = pd.DataFrame(lv), pd.DataFrame(ch)
    lv.to_csv(args.out / "periods.csv", index=False)
    ch.to_csv(args.out / "changes.csv", index=False)

    # placebo: every 6-day window inside the baseline vs the rest of the baseline
    base = wide[per == "baseline"]
    w = len(idx["window"])
    pl = []
    for s in range(len(base) - w + 1):
        inside = np.zeros(len(base), bool)
        inside[s:s + w] = True
        diff = base[inside].mean() - base[~inside].mean()
        for g in GROUPS:
            pl.append({"start": base.index[s], "group": g, "change": diff[("neg_share_equal", g)]})
    pl = pd.DataFrame(pl)
    pl.to_csv(args.out / "placebo.csv", index=False)

    main_c = "neg_share_equal"
    lines = [f"H2, measure {main_c} (share of negative comments, subreddits weighted equally)",
             f"days: " + ", ".join(f"{p} {len(idx[p])}" for p in PERIODS), ""]
    for g in GROUPS:
        r = lv[(lv.measure == main_c) & (lv.group == g)].set_index("period")
        c_ = ch[(ch.measure == main_c) & (ch.contrast == f"{g}: window - baseline")].iloc[0]
        q = (pl[pl.group == g].change >= c_.value).mean()
        lines.append(f"{g:6s} baseline {r.loc['baseline','value']:.1%} -> window {r.loc['window','value']:.1%} "
                     f"| change {c_.value:+.1%} [{c_.ci_lo:+.1%}, {c_.ci_hi:+.1%}] "
                     f"| placebo: {q:.1%} of ordinary {w}-day baseline windows rise at least as much")
    lines.append("")
    for _, r in ch[(ch.measure == main_c) & ch.contrast.str.startswith("DiD window")].iterrows():
        lines.append(f"{r.contrast}: {r.value:+.1%} [{r.ci_lo:+.1%}, {r.ci_hi:+.1%}]")
    lines.append("\nRobustness (window - baseline), all measures:")
    rob = ch[ch.contrast.str.contains("window - baseline")].pivot(index="contrast", columns="measure", values="value")
    lines.append(rob.map(lambda v: f"{v:+.1%}").to_string())
    txt = "\n".join(lines)
    (args.out / "h2_report.txt").write_text(txt + "\n", encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
