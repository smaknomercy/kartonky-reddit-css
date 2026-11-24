"""
Validation of automatic labels against the manual annotation (HW4).

Compares
  human_human      Anton vs Veronika on the shared units (Cohen's kappa, agreement)
  <model>_vs_gold  LLM labels (data/labels/llm_v1/sample_<model>/labels.jsonl) vs the gold standard
  xlmr_vs_gold     sentiment of cardiffnlp/twitter-xlm-roberta-base-sentiment vs gold sentiment
  <model>_vs_<annotator>, xlmr_vs_<annotator>  on all test units of that annotator (shared + own).
      Main reading: is a system's agreement with a person close to the agreement between people?
      This needs no adjudication; the gold comparison does and is secondary.

Gold standard
  individual units : the annotator's labels
  shared units     : the label both annotators gave; where they differ, the adjudicated label
                     from adjudication.csv; unresolved differences are left out and counted
                     (leaving them out favours easy cases, hence the per-annotator comparison).
  to_adjudicate.csv lists the key fields first (KEY_FIELDS), so a partial pass is still useful.
  Units with split=dev were used to tune the prompt: models are scored on split=test only.
  If on_topic = no, the other fields are not compared for that unit (for gold and model alike);
  an off-topic error shows up in on_topic.

Weights: comments were sampled with equal n per stratum; "weighted" rows reweight them to the
stratum sizes of the population (sampling_log.txt). Posters are a simple random sample.

Output: data/labels/validation_v1/
  metrics.csv          one row per source x field x comparison x weighting
  per_class.csv        precision / recall / F1 per class
  confusion/*.csv      confusion matrices (rows: gold, columns: predicted)
  to_adjudicate.csv    shared units where the annotators differ; fill `final` and save as adjudication.csv
  report.md            short summary

Usage
  python scripts/labeling/validate.py                      # finds returned tables and all LLM runs
  python scripts/labeling/validate.py --ann-dir DIR --llm-root DIR --xlmr FILE --out DIR
"""
import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_annotation_sample import ANNOTATORS, LABELS, TARGETS  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ANN = ROOT / "data" / "labels" / "annotation_v1"
FIELDS = [name for name, values in LABELS if values is not None and name != "unsure"]
BINARY = set(TARGETS) | {"irony"}
DERIVED = ["has_demand"]  # demand_main != none
KEY_FIELDS = ["on_topic", "speech_act", "sentiment", "fedorov_stance", "demand_main"]
XLMR_MAP = {"p_neg": "negative", "p_neu": "neutral", "p_pos": "positive"}


def norm(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return np.nan
    s = str(v).strip().lower()
    if s in ("", "nan", "none_"):
        return np.nan
    if re.fullmatch(r"[01](\.0+)?", s):
        return str(int(float(s)))
    return s


def tidy(df):
    """Normalise one annotator's (or a model's) labels: blanks in 0/1 fields are 0; off-topic blanks the rest."""
    df = df.copy()
    for f in FIELDS:
        df[f] = df[f].map(norm) if f in df else np.nan
    for f in BINARY:
        df.loc[df.on_topic.notna(), f] = df.loc[df.on_topic.notna(), f].fillna("0")
    off = df.on_topic == "no"
    df.loc[off, [f for f in FIELDS if f != "on_topic"]] = np.nan
    df["has_demand"] = df.demand_main.map(lambda v: v if pd.isna(v) else ("0" if v == "none" else "1"))
    return df


def read_annotator(path):
    frames = [pd.read_excel(path, sheet_name=s, dtype=object) for s in ("posters", "comments")]
    df = pd.concat(frames, ignore_index=True)
    return tidy(df[["unit_id"] + [c for c in df.columns if c in FIELDS]]).set_index("unit_id")


def read_llm(path):
    """Labels of the latest prompt version only (the file keeps runs of earlier prompts)."""
    recs = [json.loads(line) for line in path.open(encoding="utf-8")]
    latest = recs[-1]["prompt_sha"] if recs else None
    rows = {}
    for r in recs:
        if r.get("prompt_sha") == latest and not r.get("error") and r.get("labels"):
            rows[r["unit_id"]] = r["labels"]  # the last successful run of a unit wins
    df = pd.DataFrame.from_dict(rows, orient="index")
    df.index.name = "unit_id"
    return tidy(df.reset_index()).set_index("unit_id")


def read_xlmr(path, key):
    s = pd.read_csv(path, dtype={"id": str, "source": str})
    s["sentiment"] = s[list(XLMR_MAP)].idxmax(axis=1).map(XLMR_MAP)
    m = key.merge(s, left_on=["orig_id", "source"], right_on=["id", "source"], how="inner")
    return m.set_index("unit_id")[["sentiment"]]


def stratum_weights(key):
    log = (ANN / "sampling_log.txt").read_text(encoding="utf-8")
    sizes = dict((k, int(v.replace(",", ""))) for k, v in re.findall(r"(\w+/\w+) ([\d,]+)", log.split("stratum sizes:")[1].split("\n")[0]))
    n = key[key.source == "comment"].groupby("stratum").size()
    w = key.stratum.map(lambda s: sizes[s] / n[s] if s in sizes else 1.0)
    return pd.Series(w.values, index=key.unit_id)


def confusion(gold, pred, w, classes):
    cm = pd.DataFrame(0.0, index=classes, columns=classes)
    for g, p, ww in zip(gold, pred, w):
        cm.loc[g, p] += ww
    return cm


def scores(cm):
    tot = cm.values.sum()
    if tot == 0:
        return {}, pd.DataFrame()
    po = np.trace(cm.values) / tot
    pe = (cm.sum(1).values * cm.sum(0).values).sum() / tot ** 2
    kappa = (po - pe) / (1 - pe) if pe < 1 else np.nan
    tp = np.diag(cm.values)
    prec = np.divide(tp, cm.sum(0).values, out=np.full(len(tp), np.nan), where=cm.sum(0).values > 0)
    rec = np.divide(tp, cm.sum(1).values, out=np.full(len(tp), np.nan), where=cm.sum(1).values > 0)
    f1 = np.where((prec + rec) > 0, 2 * prec * rec / (prec + rec), 0.0)
    support = cm.sum(1).values
    present = support > 0  # macro-F1 over classes that occur in gold
    pc = pd.DataFrame({"class": cm.index, "precision": prec, "recall": rec, "f1": f1, "support": support})
    return {"accuracy": po, "kappa": kappa, "macro_f1": np.nanmean(np.where(present, f1, np.nan))}, pc


def compare(a, b, units, w, field):
    """a: reference (gold or annotator), b: compared labels; both indexed by unit_id."""
    ids = [u for u in units if u in a.index and u in b.index]
    x = pd.DataFrame({"g": a.loc[ids, field], "p": b.loc[ids, field], "w": w.reindex(ids).fillna(1.0)}).dropna()
    if x.empty:
        return None
    classes = sorted(set(x.g) | set(x.p))
    cm = confusion(x.g, x.p, x.w, classes)
    s, pc = scores(cm)
    s["n"] = len(x)
    return s, pc, cm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ann-dir", type=Path, default=ANN / "returned")
    ap.add_argument("--llm-root", type=Path, default=ROOT / "data" / "labels" / "llm_v1")
    ap.add_argument("--xlmr", type=Path, default=ROOT / "data" / "sentiment" / "xlmr_v1" / "scores.csv")
    ap.add_argument("--adjudication", type=Path, default=ANN / "adjudication.csv")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "labels" / "validation_v1")
    args = ap.parse_args()
    (args.out / "confusion").mkdir(parents=True, exist_ok=True)

    key = pd.read_csv(ANN / "sample_key.csv", dtype=str)
    src = key.set_index("unit_id").source
    w_strat = stratum_weights(key)
    ann = {a: read_annotator(args.ann_dir / f"annotation_{a}.xlsx") for a in ANNOTATORS}
    fields = FIELDS + DERIVED

    # completeness
    report = ["# Validation report", ""]
    for a, df in ann.items():
        mine = key[key.role.isin(["shared", a])].unit_id
        missing = df.reindex(mine).on_topic.isna().sum()
        report.append(f"- {a}: {len(mine) - missing}/{len(mine)} units labelled (on_topic filled)")

    # gold standard
    shared = key[key.role == "shared"].unit_id.tolist()
    a1, a2 = (ann[a] for a in ANNOTATORS)
    adj = pd.read_csv(args.adjudication, dtype=str) if args.adjudication.exists() else \
        pd.DataFrame(columns=["unit_id", "field", "final"])
    adj = {(r.unit_id, r.field): norm(r.final) for r in adj.itertuples() if pd.notna(r.final)}
    gold = pd.DataFrame(index=key.unit_id, columns=fields, dtype=object)
    todo, unresolved = [], 0
    for a, df in ann.items():
        own = key[key.role == a].unit_id
        gold.loc[own, fields] = df.reindex(own)[fields].values
    for u in shared:
        for f in fields:
            v1, v2 = a1[f].get(u, np.nan), a2[f].get(u, np.nan)
            if (u, f) in adj:
                gold.loc[u, f] = adj[(u, f)]
            elif (pd.isna(v1) and pd.isna(v2)) or v1 == v2:
                gold.loc[u, f] = v1
            else:
                unresolved += 1
                if f not in DERIVED:
                    todo.append({"unit_id": u, "source": src[u], "field": f, ANNOTATORS[0]: v1, ANNOTATORS[1]: v2, "final": ""})
    texts = pd.concat([pd.read_excel(args.ann_dir / f"annotation_{ANNOTATORS[0]}.xlsx", sheet_name=s, dtype=str)
                       for s in ("posters", "comments")]).set_index("unit_id")[["text", "context"]]
    td = pd.DataFrame(todo)
    if not td.empty:
        td["key"] = ~td.field.isin(KEY_FIELDS)
        td = td.sort_values(["key", "field", "unit_id"]).drop(columns="key")
        td = td.join(texts, on="unit_id")[["unit_id", "source", "text", "context", "field", *ANNOTATORS, "final"]]
    td.to_csv(args.out / "to_adjudicate.csv", index=False)
    report.append(f"- shared units: {unresolved} field-level disagreements unresolved "
                  f"({len(adj)} adjudicated) -> to_adjudicate.csv")

    # systems to evaluate
    systems = {}
    for d in sorted(args.llm_root.glob("sample_*")):
        if (d / "labels.jsonl").exists():
            systems[d.name.removeprefix("sample_")] = read_llm(d / "labels.jsonl")
    if args.xlmr.exists():
        systems["xlmr"] = read_xlmr(args.xlmr, key)
    report.append(f"- systems: {', '.join(systems) or 'none yet'}")

    test = set(key[key.split == "test"].unit_id)
    rows, per_class = [], []

    def add(comp, source, field, units, ref, sys_, weighted):
        w = w_strat if weighted else pd.Series(1.0, index=key.unit_id)
        r = compare(ref, sys_, units, w, field)
        if r is None:
            return
        s, pc, cm = r
        tag = f"{comp}__{source}__{field}__{'w' if weighted else 'u'}"
        rows.append({"comparison": comp, "source": source, "field": field,
                     "weighting": "weighted" if weighted else "unweighted", **s})
        per_class.append(pc.assign(comparison=comp, source=source, field=field,
                                   weighting="weighted" if weighted else "unweighted"))
        cm.to_csv(args.out / "confusion" / f"{tag}.csv")

    for source in ["poster", "comment"]:
        sh = [u for u in shared if src[u] == source]
        units_test = [u for u in key[key.source == source].unit_id if u in test]
        weightings = [False, True] if source == "comment" else [False]
        for f in fields:
            for wt in weightings:
                add("human_human", source, f, sh, a1, a2, wt)
                for name, sysdf in systems.items():
                    if f not in sysdf.columns:
                        continue
                    add(f"{name}_vs_gold", source, f, units_test, gold, sysdf, wt)
                    for a in ANNOTATORS:
                        mine = [u for u in key[(key.source == source) & key.role.isin(["shared", a])].unit_id
                                if u in test]
                        add(f"{name}_vs_{a}", source, f, mine, ann[a], sysdf, wt)

    m = pd.DataFrame(rows)
    m.to_csv(args.out / "metrics.csv", index=False)
    if per_class:
        pd.concat(per_class).to_csv(args.out / "per_class.csv", index=False)

    if not m.empty:
        u = m[m.weighting == "unweighted"]
        u = u.assign(comparison=u.comparison.str.replace(f"_vs_({'|'.join(ANNOTATORS)})$", "_vs_annotators", regex=True))
        keep = ["human_human"] + [f"{s}_vs_annotators" for s in systems] + [f"{s}_vs_gold" for s in systems]
        piv = u[u.comparison.isin(keep)].pivot_table(index=["source", "field"], columns="comparison",
                                                     values="kappa", aggfunc="mean")
        report += ["", "## Cohen's kappa, unweighted",
                   "human_human: shared units; *_vs_annotators: mean over the two annotators, their test units; "
                   "*_vs_gold: test units, unresolved disagreements left out", "",
                   piv.reindex(columns=[c for c in keep if c in piv.columns]).round(2).to_string()]
    (args.out / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))


if __name__ == "__main__":
    main()
