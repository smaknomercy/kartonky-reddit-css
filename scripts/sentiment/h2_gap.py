"""
H2, second part: is the negativity concentrated in the conversation about Fedorov?
Gap = share of negative comments about Fedorov minus share among the other comments, per group
and period, with thread-level bootstrap intervals.

Comments
  F (about Fedorov), two definitions:
    body : Fedorov in the comment body. The same rule in every month, so the baseline gap is comparable.
    pool : body, or Fedorov in the thread title (the LLM pool). Thread-title comments exist only for
           Jul-Aug (comment_pool() reads data/clean only), so pool is reported for Jul-Aug only.
  O (others): day-sample comments that are not in F (no Fedorov in body, not in a Fedorov thread).
Standardisation
  F comes mostly from a few subreddits (r/ukraine, r/worldnews). To keep the subreddit mix from
  driving the gap, O is reweighted to F's mix: gap = sum_s w_s (F_s - O_s), w_s = n_F,s / n_F.
  The raw gap (pooled F minus pooled O) is reported too.
CI
  95% percentile bootstrap over threads (link_id), resampled within subreddit x set x period:
  comments of one thread are not independent, and in the window F sits in a few large threads.
Periods
  baseline 02.03-30.06; window 15-20.07 (as in HW3; --window to change); late_july; august.

Scales
  The gap in percentage points and as an odds ratio (OR): the groups start from very different levels
  (O ~38% negative in ua, ~68% in intl), and a pp gap is capped near 100%. In gap_contrasts.csv the
  "ratio of OR" rows hold log values; the report shows them exponentiated.

Output: data/sentiment/xlmr_v1/h2/gap.csv, gap_contrasts.csv, gap_report.txt (or --out)

Usage: python scripts/sentiment/h2_gap.py [--b 5000] [--window 2026-07-15:2026-07-20] [--out DIR]
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


def periods(window):
    lo, hi = window.split(":")
    late_lo = (pd.Timestamp(hi) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    return {"baseline": ("2026-03-02", "2026-06-30"), "window": (lo, hi),
            "late_july": (late_lo, "2026-07-31"), "august": ("2026-08-01", "2026-08-31")}


def load(pers):
    u = pd.read_csv(SDIR / "units.csv", dtype=str, keep_default_na=False).drop(columns="date_local")
    m = pd.read_csv(SDIR / "unit_meta.csv", dtype={"id": str, "link_id": str, "date_local": str})
    s = pd.read_csv(SDIR / "scores.csv", dtype={"id": str, "source": str})
    d = u[u.source == "comment"].merge(m, on="id").merge(s, on=["id", "source"])
    d["neg"] = (d[["p_neg", "p_neu", "p_pos"]].idxmax(axis=1) == "p_neg").astype(int)
    d["group"] = d.subreddit.map(GROUP)
    d["period"] = ""
    for p, (lo, hi) in pers.items():
        d.loc[(d.date_local >= lo) & (d.date_local <= hi), "period"] = p
    d = d[d.period != ""]
    fed = d.fedorov == "True"
    d["set_body"] = np.where(fed & d.in_body, "F", np.where(~fed & (d.daysample == "True"), "O", ""))
    d["set_pool"] = np.where(fed, "F", np.where(d.daysample == "True", "O", ""))
    return d


def thread_table(d, setcol):
    """Per (group, subreddit, period, set, thread): n comments and n negative."""
    x = d[d[setcol] != ""]
    return x.groupby(["group", "subreddit", "period", setcol, "link_id"], as_index=False).agg(
        n=("neg", "size"), k=("neg", "sum")).rename(columns={setcol: "set"})


def boot_cells(tt, b, rng):
    """Bootstrap (n, k) totals per cell (group, subreddit, period, set): dict cell -> (n[b], k[b]), point."""
    out = {}
    for cell, x in tt.groupby(["group", "subreddit", "period", "set"]):
        t = len(x)
        draws = rng.multinomial(t, np.full(t, 1 / t), size=b)  # b x t thread counts
        out[cell] = (draws @ x.n.values, draws @ x.k.values, x.n.sum(), x.k.sum())
    return out


def gap_stats(cells, g, p, b):
    """Point and bootstrap draws of share_F, share_O (reweighted to F's mix), gap std, gap raw."""
    subs = sorted({c[1] for c in cells if c[0] == g and c[2] == p and c[3] == "F"})
    subs = [s for s in subs if (g, s, p, "O") in cells]
    if not subs:
        return None
    nF = np.array([cells[(g, s, p, "F")][2] for s in subs], float)
    w = nF / nF.sum()

    def shares(which):  # which: 0 bootstrap, 1 point
        fs, os_, fn, fk, on, ok = [], [], 0, 0, 0, 0
        for s in subs:
            Fn, Fk, Fn0, Fk0 = cells[(g, s, p, "F")]
            On, Ok, On0, Ok0 = cells[(g, s, p, "O")]
            if which:
                Fn, Fk, On, Ok = Fn0, Fk0, On0, Ok0
            with np.errstate(invalid="ignore", divide="ignore"):
                fs.append(Fk / Fn)
                os_.append(Ok / On)
            fn, fk, on, ok = fn + Fn, fk + Fk, on + On, ok + Ok
        fs, os_ = np.array(fs), np.array(os_)
        f_std = np.nansum(w[:, None] * fs, axis=0) if not which else np.nansum(w * fs)
        o_std = np.nansum(w[:, None] * os_, axis=0) if not which else np.nansum(w * os_)
        logit = lambda q: np.log(q / (1 - q))
        return {"F": fk / fn, "O_std": o_std, "O_raw": ok / on, "gap_std": f_std - o_std, "gap_raw": fk / fn - ok / on,
                "log_or": logit(f_std) - logit(o_std)}

    pt, bt = shares(1), shares(0)
    return pt, bt, int(nF.sum()), subs


def ci(a):
    return np.nanpercentile(a, [2.5, 97.5])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--b", type=int, default=5000)
    ap.add_argument("--window", default="2026-07-15:2026-07-20")
    ap.add_argument("--out", type=Path, default=SDIR / "h2")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    pers = periods(args.window)
    d = load(pers)

    rows, con, boots = [], [], {}
    for defn in ["body", "pool"]:
        tt = thread_table(d, f"set_{defn}")
        cells = boot_cells(tt, args.b, np.random.default_rng(2026))
        for g in GROUPS:
            for p in pers:
                if defn == "pool" and p == "baseline":
                    continue  # thread-title comments exist only for Jul-Aug
                r = gap_stats(cells, g, p, args.b)
                if r is None:
                    continue
                pt, bt, nF, subs = r
                boots[(defn, g, p)] = bt
                x = tt[(tt.group == g) & (tt.period == p)]
                row = {"definition": defn, "group": g, "period": p, "n_F": nF,
                       "threads_F": int((x.set == "F").sum()), "n_O": int(x[x.set == "O"].n.sum())}
                for key in ["F", "O_std", "O_raw", "gap_std", "gap_raw", "log_or"]:
                    lo, hi = ci(bt[key])
                    row.update({key: pt[key], f"{key}_lo": lo, f"{key}_hi": hi})
                rows.append(row)
        # contrasts: gap change vs baseline (body only), and gap difference between groups
        for g in GROUPS:
            for p in ["window", "late_july", "august"]:
                if defn == "body" and (defn, g, p) in boots and (defn, g, "baseline") in boots:
                    a, b0 = boots[(defn, g, p)]["gap_std"], boots[(defn, g, "baseline")]["gap_std"]
                    pv = [r_ for r_ in rows if r_["definition"] == defn and r_["group"] == g]
                    v = {r_["period"]: r_["gap_std"] for r_ in pv}
                    lo, hi = ci(a - b0)
                    con.append({"definition": defn, "contrast": f"{g}: gap {p} - gap baseline",
                                "value": v[p] - v["baseline"], "ci_lo": lo, "ci_hi": hi})
        for a_, b_ in [("ua", "intl"), ("ua", "ua_en"), ("ua_en", "intl")]:
            ka, kb = (defn, a_, "window"), (defn, b_, "window")
            va = [r_["gap_std"] for r_ in rows if (r_["definition"], r_["group"], r_["period"]) == ka][0]
            vb = [r_["gap_std"] for r_ in rows if (r_["definition"], r_["group"], r_["period"]) == kb][0]
            lo, hi = ci(boots[ka]["gap_std"] - boots[kb]["gap_std"])
            con.append({"definition": defn, "contrast": f"window gap: {a_} - {b_}", "value": va - vb,
                        "ci_lo": lo, "ci_hi": hi})
            # the same on the log-odds scale: a pp gap is capped near 100%, and intl starts from ~68% negative
            la = [r_["log_or"] for r_ in rows if (r_["definition"], r_["group"], r_["period"]) == ka][0]
            lb = [r_["log_or"] for r_ in rows if (r_["definition"], r_["group"], r_["period"]) == kb][0]
            lo, hi = ci(boots[ka]["log_or"] - boots[kb]["log_or"])
            con.append({"definition": defn, "contrast": f"window ratio of OR: {a_} / {b_}", "value": la - lb,
                        "ci_lo": lo, "ci_hi": hi})

    res, con = pd.DataFrame(rows), pd.DataFrame(con)
    res.to_csv(args.out / "gap.csv", index=False)
    con.to_csv(args.out / "gap_contrasts.csv", index=False)

    f = lambda v: f"{v:.1%}"
    fp = lambda v: f"{v:+.1%}"
    lines = [f"H2 gap: negative share of comments about Fedorov (F) vs other comments (O, reweighted to F's subreddit mix)",
             f"window {args.window}; 95% CI: bootstrap over threads, B={args.b}", ""]
    for defn in ["body", "pool"]:
        lines.append(f"== F = {'Fedorov in the comment body' if defn == 'body' else 'body or thread title (Jul-Aug only)'}")
        for _, r in res[res.definition == defn].iterrows():
            lines.append(f"{r.group:6s} {r.period:10s} F {f(r.F)} (n={r.n_F}, {r.threads_F} threads) | O {f(r.O_std)} "
                         f"| gap {fp(r.gap_std)} [{fp(r.gap_std_lo)}, {fp(r.gap_std_hi)}] | raw gap {fp(r.gap_raw)} "
                         f"| OR {np.exp(r.log_or):.2f} [{np.exp(r.log_or_lo):.2f}, {np.exp(r.log_or_hi):.2f}]")
        lines.append("")
    lines.append("Contrasts (standardised gap):")
    for _, r in con.iterrows():
        if "ratio of OR" in r.contrast:
            sig = "excludes 1" if r.ci_lo > 0 or r.ci_hi < 0 else "includes 1"
            lines.append(f"[{r.definition}] {r.contrast}: {np.exp(r.value):.2f} [{np.exp(r.ci_lo):.2f}, {np.exp(r.ci_hi):.2f}] ({sig})")
            continue
        sig = "excludes 0" if r.ci_lo > 0 or r.ci_hi < 0 else "includes 0"
        lines.append(f"[{r.definition}] {r.contrast}: {fp(r.value)} [{fp(r.ci_lo)}, {fp(r.ci_hi)}] ({sig})")
    txt = "\n".join(lines)
    (args.out / "gap_report.txt").write_text(txt + "\n", encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
