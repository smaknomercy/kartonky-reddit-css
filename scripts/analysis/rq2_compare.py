"""
RQ2 "What": what did the street say (posters) and what did Reddit discuss (comments)?
Compares the shares of every label of the annotation scheme between posters and comments
(and the three comment groups), with cluster-bootstrap CIs, a chi-square test and Cohen's w.

Units: on-topic units only (on_topic == yes). Labels: the LLM labels of the latest prompt
(validate.read_llm). After the Friday validation the shares get a correction for the LLM's
errors (not here yet).

Scopes
  sample : the 600 units of the manual annotation sample (annotation_v1). Comments were drawn
           with equal n per stratum (group x body/thread), so they are reweighted to the stratum
           sizes of the pool (validate.stratum_weights). Posters are a simple random sample.
  all    : every labelled unit (llm_label.py --scope all); no weights.
Fields
  categorical: speech_act, demand_main (incl. none), fedorov_stance, reference, sentiment
  binary     : target_* (addressees), irony, has_demand (demand_main != none)
CI
  95% percentile bootstrap. Clusters are resampled within strata: a comment's cluster is its
  thread (link_id; comments of one thread are not independent), a poster is its own cluster.
Test
  Chi-square on the poster vs comment table, with design-adjusted counts (each source's weighted
  shares x its unweighted n), classes with expected count < 5 merged. Cohen's w = sqrt(chi2 / N):
  0.1 small, 0.3 medium, 0.5 large. The p-value ignores clustering (anti-conservative): read the
  bootstrap CIs and w first.

Output: data/analysis/rq2_<scope>/
  shares.csv   field, class, source, n (on-topic units), share, ci_lo, ci_hi
  diffs.csv    field, class, contrast, diff, ci_lo, ci_hi
  tests.csv    field, chi2, df, p, w, N, merged
  report.md    the main numbers in words (Ukrainian)

Usage: python scripts/analysis/rq2_compare.py [--scope sample|all] [--labels PATH] [--b 2000]
"""
import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "labeling"))
import validate as V  # noqa: E402
from make_annotation_sample import LABELS, SUB_GROUP, TARGETS, comment_pool  # noqa: E402

DATA = ROOT / "data"
CATEGORICAL = ["speech_act", "demand_main", "fedorov_stance", "reference", "sentiment"]
BINARY = TARGETS + ["irony", "has_demand"]
CLASSES = {name: values.split(",") for name, values in LABELS if values and values != "0,1"}
SOURCES = ["poster", "comment", "comment_ua", "comment_ua_en", "comment_intl"]
SOURCE_UA = {"poster": "плакати", "comment": "коментарі", "comment_ua": "коментарі ua",
             "comment_ua_en": "коментарі ua_en", "comment_intl": "коментарі intl"}


# ---------- statistics without scipy ----------

def chi2_sf(x, df):
    """P(X > x) for chi-square with df degrees of freedom: regularised upper incomplete gamma Q(df/2, x/2)."""
    a, x = df / 2, x / 2
    if x <= 0:
        return 1.0
    if x < a + 1:  # series for P, then Q = 1 - P
        term = total = 1 / a
        n = a
        for _ in range(1000):
            n += 1
            term *= x / n
            total += term
            if abs(term) < abs(total) * 1e-12:
                break
        return max(0.0, 1 - total * math.exp(-x + a * math.log(x) - math.lgamma(a)))
    # continued fraction for Q (Lentz)
    b, c, d = x + 1 - a, 1e300, 1 / (x + 1 - a)
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-12:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def chi2_test(table):
    """table: 2 x k counts (rows: sources). Merges columns with expected < 5 into one. -> chi2, df, p, w, N, merged."""
    t = np.asarray(table, float)
    N = t.sum()
    exp = t.sum(1, keepdims=True) * t.sum(0, keepdims=True) / N
    small = (exp < 5).any(0)
    merged = int(small.sum())
    if merged and (~small).sum() >= 1:
        t = np.column_stack([t[:, ~small], t[:, small].sum(1)]) if merged > 0 else t
        exp = t.sum(1, keepdims=True) * t.sum(0, keepdims=True) / N
        keep = exp.min(0) > 0
        t, exp = t[:, keep], exp[:, keep]
    chi2 = float(((t - exp) ** 2 / exp).sum())
    df = (t.shape[0] - 1) * (t.shape[1] - 1)
    p = chi2_sf(chi2, df) if df > 0 else float("nan")
    return chi2, df, p, math.sqrt(chi2 / N), int(N), merged


# ---------- data ----------

def load_units(scope, labels_path):
    lab = V.read_llm(labels_path).reset_index()
    if scope == "sample":
        key = pd.read_csv(V.ANN / "sample_key.csv", dtype=str)
        w = V.stratum_weights(key)
        d = lab.merge(key[["unit_id", "source", "orig_id", "stratum"]], on="unit_id")
        d["weight"] = d.unit_id.map(w).where(d.source == "comment", 1.0)
        d["group"] = np.where(d.source == "comment", d.stratum.str.split("/").str[0], "")
        meta = pd.read_csv(DATA / "sentiment" / "xlmr_v1" / "unit_meta.csv", dtype=str)
        thread = dict(zip(meta.id, meta.link_id))
        d["cluster"] = np.where(d.source == "comment", d.orig_id.map(thread), "P" + d.unit_id)
    else:
        d = lab.copy()
        d["source"] = np.where(d.unit_id.str.startswith("C_"), "comment", "poster")
        d["orig_id"] = d.unit_id.str[2:]
        pool = comment_pool([])[["id", "subreddit", "link_id"]]
        d = d.merge(pool.rename(columns={"id": "orig_id"}), on="orig_id", how="left")
        d["group"] = d.subreddit.map(SUB_GROUP).fillna("")
        d["stratum"] = np.where(d.source == "comment", d.group, "poster")
        d["weight"] = 1.0
        d["cluster"] = np.where(d.source == "comment", d.link_id, d.unit_id)
    d.loc[d.source == "poster", "stratum"] = "poster"
    if d.cluster.isna().any():
        print(f"warning: {d.cluster.isna().sum()} comments without a thread; each becomes its own cluster")
        d["cluster"] = d.cluster.fillna(d.unit_id)
    return d


def indicator_matrix(d):
    """One 0/1 column per (field, class); NaN where the field is missing for a unit."""
    cols, names = [], []
    for f in CATEGORICAL:
        for c in CLASSES[f]:
            cols.append(np.where(d[f].isna(), np.nan, (d[f] == c).astype(float)))
            names.append((f, c))
    for f in BINARY:
        cols.append(np.where(d[f].isna(), np.nan, (d[f] == "1").astype(float)))
        names.append((f, "1"))
    return np.column_stack(cols), names


def source_masks(d):
    return {"poster": (d.source == "poster").values, "comment": (d.source == "comment").values,
            **{f"comment_{g}": ((d.source == "comment") & (d.group == g)).values for g in ["ua", "ua_en", "intl"]}}


def weighted_shares(X, w, masks):
    """-> {source: shares[cols]} with NaN-aware denominators. w: (units,) or (B, units)."""
    valid = ~np.isnan(X)
    Xz = np.nan_to_num(X)
    out = {}
    for s, m in masks.items():
        ws = w[..., m]
        out[s] = (ws @ Xz[m]) / np.maximum(ws @ valid[m], 1e-12)
    return out


def bootstrap_weights(d, b, rng, chunk=250):
    """Yields (chunk, units) multiplicity x weight: clusters resampled within strata."""
    groups = []
    for _, x in d.groupby("stratum"):
        cl, inv = np.unique(x.cluster.values, return_inverse=True)
        groups.append((x.index.values, inv, len(cl)))
    w = d.weight.values
    for start in range(0, b, chunk):
        k = min(chunk, b - start)
        m = np.zeros((k, len(d)))
        for idx, inv, ncl in groups:
            draws = rng.multinomial(ncl, np.full(ncl, 1 / ncl), size=k)  # k x clusters
            m[:, idx] = draws[:, inv]
        yield m * w


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=["sample", "all"], default="sample")
    ap.add_argument("--labels", type=Path, help="labels.jsonl (default: llm_v1/<scope>_claude-haiku-5-5)")
    ap.add_argument("--b", type=int, default=2000)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    labels = args.labels or DATA / "labels" / "llm_v1" / f"{args.scope}_claude-haiku-5-5" / "labels.jsonl"
    out = args.out or DATA / "analysis" / f"rq2_{args.scope}"
    out.mkdir(parents=True, exist_ok=True)

    d = load_units(args.scope, labels)
    n_all = d.source.value_counts().to_dict()
    d = d[d.on_topic == "yes"].reset_index(drop=True)
    X, names = indicator_matrix(d)
    masks = source_masks(d)
    point = weighted_shares(X, d.weight.values, masks)

    boot = {s: [] for s in masks}
    for wb in bootstrap_weights(d, args.b, np.random.default_rng(2026)):
        for s, v in weighted_shares(X, wb, masks).items():
            boot[s].append(v)
    boot = {s: np.vstack(v) for s, v in boot.items()}

    rows, diffs = [], []
    for j, (f, c) in enumerate(names):
        for s in masks:
            lo, hi = np.nanpercentile(boot[s][:, j], [2.5, 97.5])
            rows.append({"field": f, "class": c, "source": s, "n": int(masks[s].sum()),
                         "share": point[s][j], "ci_lo": lo, "ci_hi": hi})
        for s in ["comment", "comment_ua", "comment_ua_en", "comment_intl"]:
            dd = boot["poster"][:, j] - boot[s][:, j]
            lo, hi = np.nanpercentile(dd, [2.5, 97.5])
            diffs.append({"field": f, "class": c, "contrast": f"poster - {s}",
                          "diff": point["poster"][j] - point[s][j], "ci_lo": lo, "ci_hi": hi})
    shares, diffs = pd.DataFrame(rows), pd.DataFrame(diffs)
    shares.to_csv(out / "shares.csv", index=False)
    diffs.to_csv(out / "diffs.csv", index=False)

    # chi-square poster vs comment, design-adjusted counts
    n_p, n_c = int(masks["poster"].sum()), int(masks["comment"].sum())
    tests = []
    for f in CATEGORICAL + BINARY:
        cls = CLASSES.get(f, ["1"])
        idx = [names.index((f, c)) for c in cls]
        sp, sc = point["poster"][idx], point["comment"][idx]
        if f in BINARY:
            sp, sc = np.r_[sp, 1 - sp], np.r_[sc, 1 - sc]
        chi2, df, p, w, N, merged = chi2_test([sp * n_p, sc * n_c])
        tests.append({"field": f, "chi2": chi2, "df": df, "p": p, "w": w, "N": N, "merged": merged})
    tests = pd.DataFrame(tests)
    tests.to_csv(out / "tests.csv", index=False)

    # report
    pc = lambda v: f"{v * 100:.0f}%"  # noqa: E731
    pp = lambda v: "0" if round(v * 100) == 0 else f"{v * 100:+.0f}".replace("-", "−")  # noqa: E731
    L = [f"# RQ2: плакати проти коментарів ({args.scope}, мітки LLM без поправки)", "",
         f"Одиниць: {n_all}; на тему (on_topic = yes): плакатів {n_p}, коментарів {n_c} "
         f"(ua {masks['comment_ua'].sum()}, ua_en {masks['comment_ua_en'].sum()}, intl {masks['comment_intl'].sum()}). "
         f"95% ДІ: бутстреп по тредах/плакатах, B = {args.b}." + (" Коментарі перезважено під розміри страт пулу." if args.scope == "sample" else ""),
         "", "w Коена: 0,1 малий, 0,3 середній, 0,5 великий ефект. p не враховує кластери, тож він оптимістичний.", ""]
    for f in CATEGORICAL + BINARY:
        t = tests[tests.field == f].iloc[0]
        L.append(f"## {f}: w = {t.w:.2f}, χ² = {t.chi2:.1f}, df = {t.df}, p = {t.p:.2g}"
                 + (f" (рідкісних класів об'єднано: {t.merged})" if t.merged else ""))
        L.append("| клас | плакати | коментарі | різниця, в. п. | ua | ua_en | intl |")
        L.append("|---|---|---|---|---|---|---|")
        for c in CLASSES.get(f, ["1"]):
            g = lambda s: shares[(shares.field == f) & (shares["class"] == c) & (shares.source == s)].iloc[0]  # noqa: E731
            dd = diffs[(diffs.field == f) & (diffs["class"] == c) & (diffs.contrast == "poster - comment")].iloc[0]
            ps, cs = g("poster"), g("comment")
            L.append(f"| {c} | {pc(ps.share)} [{pc(ps.ci_lo)}; {pc(ps.ci_hi)}] | {pc(cs.share)} [{pc(cs.ci_lo)}; {pc(cs.ci_hi)}] "
                     f"| {pp(dd['diff'])} [{pp(dd.ci_lo)}; {pp(dd.ci_hi)}] | {pc(g('comment_ua').share)} "
                     f"| {pc(g('comment_ua_en').share)} | {pc(g('comment_intl').share)} |")
        L.append("")
    (out / "report.md").write_text("\n".join(L), encoding="utf-8")
    print(f"saved to {out}")
    print(tests.to_string(index=False, float_format=lambda v: f"{v:.3g}"))


if __name__ == "__main__":
    main()
