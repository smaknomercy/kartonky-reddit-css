"""
H2 charts (also the first test of theme.py).

  h2_tone_daily : share of negative comments per group, Mar-Aug, 7-day rolling mean
  h2_fedorov_gap: negative share of comments about Fedorov vs other comments, spring vs protest window

Input : data/sentiment/xlmr_v1/h2/{group_day,gap}.csv (h2_did.py, h2_gap.py)
Output: reports/figures/hw4/
Usage : python scripts/viz/fig_h2.py
"""
import sys
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from theme import (BOLD, CONTEXT, GROUP_COLORS, GROUP_LABELS, GROUPS, INK, INK2, MARKER, SURFACE, apply,  # noqa: E402
                   end_label, mark_period, pct, save, source, title)

ROOT = Path(__file__).resolve().parents[2]
H2 = ROOT / "data" / "sentiment" / "xlmr_v1" / "h2"
MONTHS = ["", "січ", "лют", "бер", "квіт", "трав", "черв", "лип", "серп", "вер", "жовт", "лист", "груд"]


def tone_daily():
    gd = pd.read_csv(H2 / "group_day.csv", parse_dates=["date_local"])
    gd = gd[gd.date_local >= "2026-03-02"]
    fig, ax = plt.subplots(figsize=(6.3, 3.7))
    mark_period(ax, pd.Timestamp("2026-07-15"), pd.Timestamp("2026-07-20") + pd.Timedelta(days=1),
                "протести\n15–20.07")
    for g in GROUPS:
        s = gd[gd.group == g].set_index("date_local").neg_share_equal.rolling(7, center=True, min_periods=4).mean()
        ax.plot(s.index, s.values, color=GROUP_COLORS[g], lw=1.8)
        last = s.dropna()
        end_label(ax, last.index[-1], last.iloc[-1], GROUP_LABELS[g], GROUP_COLORS[g])
    ax.set_ylim(0.25, 0.85)
    pct(ax)
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: MONTHS[mdates.num2date(v).month]))
    ax.set_xlim(pd.Timestamp("2026-03-01"), pd.Timestamp("2026-09-01"))
    ax.set_ylabel("частка негативних коментарів")
    title(fig, "Протести не зробили Reddit загалом негативнішим",
          "Частка негативних коментарів, ковзне середнє за 7 днів; сабредіти в групі мають рівні ваги")
    source(fig, "Reddit, 8 сабредітів, 2.03–31.08.2026, до 60 коментарів на сабредіт на день.\n"
                "Тональність: xlm-roberta (cardiffnlp), ще не звірена з ручною розміткою")
    return save(fig, "h2_tone_daily", top=0.80, bottom=0.15, left=0.09, right=0.77)


def fedorov_gap():
    gap = pd.read_csv(H2 / "gap.csv")
    gap = gap[(gap.definition == "body") & gap.period.isin(["baseline", "window"])]
    fig, ax = plt.subplots(figsize=(6.3, 3.9))
    rows, y = [], 0
    for g in GROUPS:
        for p, plabel in [("baseline", "березень–червень"), ("window", "15–20 липня")]:
            r = gap[(gap.group == g) & (gap.period == p)].iloc[0]
            ax.plot([r.O_std, r.F], [y, y], color=CONTEXT, lw=1, zorder=1, ls=(0, (2, 2)))
            # CI as a soft band behind the dot, so it does not read as the connector
            ax.plot([r.F_lo, r.F_hi], [y, y], color=MARKER, lw=5, alpha=0.25, zorder=2, solid_capstyle="round")
            ax.plot(r.O_std, y, "o", color=CONTEXT, ms=7, mec=SURFACE, mew=1.5, zorder=3)
            ax.plot(r.F, y, "o", color=MARKER, ms=7, mec=SURFACE, mew=1.5, zorder=3)
            ax.annotate(f"{r.gap_std * 100:+.0f} п.п.".replace("-", "−"), (max(r.F_hi, r.O_std), y), xytext=(8, 0),
                        textcoords="offset points", va="center", fontsize=8.5, color=INK)
            rows.append((y, plabel, g if p == "baseline" else None))
            y += 1
        y += 0.6
    ax.set_yticks([r[0] for r in rows], [r[1] for r in rows], fontsize=8.5, color=INK2)
    for yy, _, g in rows:
        if g:
            ax.text(-0.02, yy - 0.75, GROUP_LABELS[g], transform=ax.get_yaxis_transform(), ha="right",
                    va="center", fontsize=9.5, color=INK, fontproperties=BOLD)
    ax.invert_yaxis()
    ax.set_xlim(0.3, 1.0)
    pct(ax, "x")
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", visible=True)
    ax.set_xlabel("частка негативних коментарів")
    # legend as direct labels on the first row
    first = gap[(gap.group == "ua") & (gap.period == "baseline")].iloc[0]
    ax.annotate("інші коментарі", (first.O_std, rows[0][0]), xytext=(0, 9), textcoords="offset points",
                ha="center", fontsize=8, color=INK2)
    ax.annotate("про Федорова", (first.F, rows[0][0]), xytext=(0, 9), textcoords="offset points",
                ha="center", fontsize=8, color=MARKER)
    title(fig, "Негатив зосереджений у розмові про Федорова",
          "Частка негативних коментарів зі згадкою Федорова і без неї; смуга — 95% CI (bootstrap по тредах)")
    source(fig, "Інші коментарі перезважено під склад сабредітів коментарів про Федорова. "
                "Навесні коментарів про Федорова 43–70 на групу.\nТональність: xlm-roberta, ще не звірена з ручною розміткою")
    return save(fig, "h2_fedorov_gap", top=0.75, bottom=0.17, left=0.30, right=0.93)


if __name__ == "__main__":
    apply()
    for f in (tone_daily, fedorov_gap):
        print(f())
