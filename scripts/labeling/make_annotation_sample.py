"""
Draw the manual annotation sample for H4 and build one spreadsheet per annotator.

Posters : unique poster texts (by text_norm), simple random sample.
Comments: the population the LLM will label, i.e. comments in our 8 subreddits that
          mention Fedorov in the body ("body") or sit in a thread whose title mentions
          him ("thread"; title taken from submissions via link_id, since link_title is
          mostly empty in the dumps); removed texts and AutoModerator excluded. Stratified:
          3 subreddit groups x 2 mention types, equal n per stratum.

Within each source, units are split round-robin per stratum into
  shared   (both annotators, for Cohen's kappa)
  anton / veronika (one annotator each)
and a few individual units are marked split=dev (prompt tuning), the rest split=test.

Outputs (data/labels/annotation_v1/):
  annotation_anton.xlsx, annotation_veronika.xlsx   dropdowns via data validation
  sample_key.csv                                    unit_id -> original id, stratum, role, split
  sampling_log.txt                                  population and stratum sizes (for weights)

Usage: python scripts/labeling/make_annotation_sample.py [--n-posters 300] [--n-comments-per-stratum 50]
"""
import argparse
import random
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = Path(__file__).resolve().parents[2]
CLEAN = ROOT / "data" / "clean"
OUT = ROOT / "data" / "labels" / "annotation_v1"
SEED = 2026
ANNOTATORS = ["anton", "veronika"]

# Same Fedorov pattern as scripts/reddit/dump_pass.py
FEDOROV = r"fedorov(?!k|i?ch|ych)|ф[её]доров(?!к|ич)"
SUB_GROUP = {
    "reddit_ukr": "ua", "RedditUATalks": "ua", "Kyiv": "ua",
    "ukraine": "ua_en", "UkrainianConflict": "ua_en",
    "worldnews": "intl", "europe": "intl", "UkraineRussiaReport": "intl",
}

# Label columns and allowed values (must match prompts/annotation_guide.md)
TARGETS = ["target_fedorov", "target_syrskyi", "target_zelensky", "target_parliament",
           "target_government", "target_system", "target_russia", "target_society"]
DEMANDS = "return_fedorov,dismiss_syrskyi,other_personnel,listen_explain,military_policy,elections,other,none"
LABELS = [
    ("on_topic", "yes,no,unsure"),
    *((t, "0,1") for t in TARGETS),
    ("fedorov_stance", "support,against,neutral,not_mentioned"),
    ("speech_act", "demand,rally,support,outrage,discussion,information,other"),
    ("irony", "0,1"),
    ("demand_main", DEMANDS),
    ("demand_second", DEMANDS),
    ("reference", "meme_popculture,past_protest,history_literature,other,none"),
    ("sentiment", "negative,neutral,positive"),
    ("unsure", "0,1"),
    ("comment", None),
]
BASE_COLS = ["unit_id", "part", "source", "text", "context"]


def sample_posters(n, rng_state, log):
    k = pd.read_csv(CLEAN / "kartonky_clean.csv")
    k = k[k.has_text & k.text.notna()]
    uniq = k.drop_duplicates("text_norm")
    log.append(f"posters: {len(k):,} with text, {len(uniq):,} unique texts (by text_norm), sample {n}")
    s = uniq.sample(n, random_state=rng_state).copy()
    city = s.city_norm.fillna("місто невідоме")
    date = s.date_local.fillna("дата невідома") + s.date_reliable.map({True: "", False: " (дата приблизна)"})
    return pd.DataFrame({
        "orig_id": s.id, "source": "poster", "stratum": "poster",
        "text": s.text, "context": city + ", " + date,
    })


def comment_pool(log):
    """Comments the LLM labels: our 8 subreddits, Fedorov in the body or the thread title."""
    cols = ["id", "link_id", "subreddit", "body", "link_title", "text_removed", "is_automod"]
    d = pd.read_csv(CLEAN / "reddit_comments.csv", usecols=cols, dtype=str, keep_default_na=False)
    d = d[(d.text_removed != "True") & (d.is_automod != "True")]
    # link_title is empty in ~89% of comments in the dumps: take the thread title from submissions
    subs = pd.read_csv(CLEAN / "reddit_submissions.csv", usecols=["id", "title"], dtype=str, keep_default_na=False)
    titles = dict(zip("t3_" + subs.id, subs.title))
    d["link_title"] = d.link_title.where(d.link_title != "", d.link_id.map(titles)).fillna("")
    log.append(f"thread title missing after join with submissions: {(d.link_title == '').mean():.1%} of comments")
    in_body = d.body.str.contains(FEDOROV, case=False, regex=True)
    in_title = d.link_title.str.contains(FEDOROV, case=False, regex=True)
    pop = d[in_body | in_title].copy()
    pop["mention"] = in_body[in_body | in_title].map({True: "body", False: "thread"})
    pop["stratum"] = pop.subreddit.map(SUB_GROUP) + "/" + pop.mention
    log.append(f"comments population (our 8, not removed, not AutoModerator, Fedorov in body or thread title): {len(pop):,}")
    log.append("stratum sizes: " + ", ".join(f"{k} {v:,}" for k, v in pop.stratum.value_counts().sort_index().items()))
    return pop


def sample_comments(n_per_stratum, rng_state, log):
    pop = comment_pool(log)
    s = pop.groupby("stratum", group_keys=False).sample(n_per_stratum, random_state=rng_state)
    return pd.DataFrame({
        "orig_id": s.id, "source": "comment", "stratum": s.stratum,
        "text": s.body, "context": "r/" + s.subreddit + " · " + s.link_title,
    })


def assign_roles(units, prefix, n_dev, rng):
    """Round-robin per stratum: shared / anton / veronika; random unit ids; dev from individual units."""
    units = units.sample(frac=1, random_state=rng.randint(0, 10**6)).reset_index(drop=True)
    roles = ["shared", *ANNOTATORS]
    # rotate the starting role per stratum, so that leftovers (50 = 17+17+16) even out across roles
    offset = units.stratum.map({s: i for i, s in enumerate(sorted(units.stratum.unique()))})
    units["role"] = [roles[(i + o) % 3] for i, o in zip(units.groupby("stratum").cumcount(), offset)]
    ids = list(range(1, len(units) + 1))
    rng.shuffle(ids)
    units["unit_id"] = [f"{prefix}{i:03d}" for i in ids]
    units["split"] = "test"
    for a in ANNOTATORS:
        own = units.index[units.role == a].tolist()
        for i in rng.sample(own, n_dev // len(ANNOTATORS)):
            units.loc[i, "split"] = "dev"
    return units


def write_sheet(ws, rows):
    header = BASE_COLS + [c for c, _ in LABELS]
    ws.append(header)
    for r in rows.itertuples():
        ws.append([r.unit_id, "спільна" if r.role == "shared" else "своя", r.source, r.text, r.context])
    n = len(rows) + 1
    bold, wrap = Font(bold=True), Alignment(wrap_text=True, vertical="top")
    shared_fill = PatternFill("solid", fgColor="EEF3FB")
    for cell in ws[1]:
        cell.font = bold
    for row in ws.iter_rows(min_row=2, max_row=n):
        for cell in row:
            cell.alignment = wrap
        if row[1].value == "спільна":
            for cell in row[:5]:
                cell.fill = shared_fill
    widths = {"unit_id": 9, "part": 9, "source": 9, "text": 70, "context": 32, "comment": 30}
    for j, col in enumerate(header, start=1):
        letter = ws.cell(1, j).column_letter
        ws.column_dimensions[letter].width = widths.get(col, 14)
        values = dict(LABELS).get(col)
        if values:
            dv = DataValidation(type="list", formula1=f'"{values}"', allow_blank=True,
                                showErrorMessage=True, error=f"Дозволено: {values}")
            ws.add_data_validation(dv)
            dv.add(f"{letter}2:{letter}{n}")
    ws.freeze_panes = "E2"


def write_readme(ws, name):
    lines = [
        f"Розмітка ДЗ 4 · анотатор: {name}",
        "",
        "Повна інструкція: annotation_guide (лежить поруч у цій папці). Прочитайте її до початку.",
        "",
        "Аркуші: posters (плакати) і comments (коментарі Reddit).",
        "Спочатку розмічайте рядки «спільна» (підсвічені блакитним), потім «своя».",
        "Заповнюйте колонки зліва направо, значення обирайте зі списку.",
        "Колонки target_*, irony, unsure: 1 = так, порожньо або 0 = ні.",
        "Якщо on_topic = no, решту полів у рядку не заповнюйте.",
        "",
        "Без ChatGPT, Claude та інших LLM. Не обговорюйте розмітку між собою, поки обидва не закінчите.",
        "Сумніваєтесь: найкращий варіант + unsure = 1 + коротко в comment.",
        "",
        "Дедлайн: середа, 21:00.",
    ]
    for line in lines:
        ws.append([line])
    ws["A1"].font = Font(bold=True, size=13)
    ws.column_dimensions["A"].width = 110


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-posters", type=int, default=300)
    ap.add_argument("--n-comments-per-stratum", type=int, default=50)
    ap.add_argument("--n-dev", type=int, default=26, help="per source, split between annotators")
    args = ap.parse_args()

    rng = random.Random(SEED)
    log = [f"seed {SEED}"]
    posters = assign_roles(sample_posters(args.n_posters, SEED, log), "P", args.n_dev, rng)
    comments = assign_roles(sample_comments(args.n_comments_per_stratum, SEED, log), "C", args.n_dev, rng)
    key = pd.concat([posters, comments], ignore_index=True)

    OUT.mkdir(parents=True, exist_ok=True)
    for name in ANNOTATORS:
        wb = Workbook()
        write_readme(wb.active, name)
        wb.active.title = "README"
        for src, df in (("posters", posters), ("comments", comments)):
            mine = df[df.role.isin(["shared", name])].copy()
            # shared first (same order for both annotators), then own units
            mine["order"] = (mine.role != "shared").astype(int)
            mine = mine.sort_values(["order", "unit_id"])
            write_sheet(wb.create_sheet(src), mine)
        wb.save(OUT / f"annotation_{name}.xlsx")

    key[["unit_id", "source", "orig_id", "stratum", "role", "split"]].sort_values("unit_id") \
        .to_csv(OUT / "sample_key.csv", index=False)
    log.append("roles: " + key.groupby(["source", "role"]).size().to_string().replace("\n", "; "))
    log.append("split: " + key.groupby(["source", "split"]).size().to_string().replace("\n", "; "))
    log.append("comment strata x role:\n" + pd.crosstab(comments.stratum, comments.role).to_string())
    (OUT / "sampling_log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
    print("\n".join(log))


if __name__ == "__main__":
    main()
