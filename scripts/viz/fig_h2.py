"""
H2 charts, one message each (details go to the text and the appendix).

  h2_tone    : the protest days vs ordinary spring weeks, change of the negative share per group
  h2_fedorov : how much more negative comments about Fedorov are than other comments, spring vs protests

Input : data/sentiment/xlmr_v1/h2/{changes,placebo,gap}.csv (h2_did.py, h2_gap.py)
Output: reports/figures/hw4/
Usage : python scripts/viz/fig_h2.py
"""
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from theme import (BAND, BOLD, CONTEXT, GROUP_LABELS, GROUPS, INK, INK2, MARKER, PP, SURFACE, WIDTH,  # noqa: E402
                   apply, header, note, num, save)

ROOT = Path(__file__).resolve().parents[2]
H2 = ROOT / "data" / "sentiment" / "xlmr_v1" / "h2"
FIG_NO = {"tone": 1, "fedorov": 2}  # final numbers are set when the report is assembled
ROW = {"ua": 2, "ua_en": 1, "intl": 0}


def tone():
    ch = pd.read_csv(H2 / "changes.csv")
    ch = ch[ch.measure == "neg_share_equal"]
    pl = pd.read_csv(H2 / "placebo.csv")
    fig = plt.figure(figsize=(WIDTH, 3.05))
    ax = fig.add_axes([0.25, 0.26, 0.70, 0.44])
    for g in GROUPS:
        y = ROW[g]
        # ordinary weeks: the middle 90% of the 116 spring windows, as a band
        lo, hi = pl[pl.group == g].change.quantile([0.05, 0.95]) * 100
        ax.fill_between([lo, hi], y - 0.22, y + 0.22, color=BAND, lw=0, zorder=0)
        r = ch[ch.contrast == f"{g}: window - baseline"].iloc[0]
        ax.hlines(y, r.ci_lo * 100, r.ci_hi * 100, color=MARKER, lw=1.8)
        ax.plot(r.value * 100, y, "o", color=MARKER, ms=6.5, mec=SURFACE, mew=1.3, zorder=3)
        # value beside the far end of its interval, away from the band and the zero line
        if r.value < 0:
            ax.text(r.ci_lo * 100 - 0.3, y, num(r.value * 100), ha="right", va="center", fontsize=8.5, color=INK2)
        else:
            ax.text(r.ci_hi * 100 + 0.3, y, num(r.value * 100), ha="left", va="center", fontsize=8.5, color=INK2)
    # legend above the plot: what the band and the red mark are
    ax.fill_between([-7.8, -7.0], 2.55, 2.75, color=BAND, lw=0, clip_on=False)
    ax.text(-6.8, 2.65, "звичайні тижні весни (90% з них)", ha="left", va="center", fontsize=7.5, color=INK2)
    ax.hlines(2.65, 2.2, 3.6, color=MARKER, lw=1.8, clip_on=False)
    ax.plot(2.9, 2.65, "o", color=MARKER, ms=5.5, mec=SURFACE, mew=1.2, clip_on=False)
    ax.text(3.9, 2.65, "15–20.07 з 95% довірчим інтервалом", ha="left", va="center", fontsize=7.5, color=INK2)
    ax.axvline(0, color=INK2, lw=0.8, zorder=0)
    ax.set_yticks([ROW[g] for g in GROUPS], [GROUP_LABELS[g] for g in GROUPS])
    ax.set_ylim(-0.4, 2.45)
    ax.set_xlim(-9, 11)
    ticks = list(range(-8, 11, 2))  # -9 leaves room for the intl value label
    ax.set_xticks(ticks, [num(t, 0) if t else "0" for t in ticks[:-1]] + [f"{num(ticks[-1], 0)} {PP}"])
    header(fig, FIG_NO["tone"], "Під час протестів Reddit не став негативнішим, ніж у звичайні тижні",
           "Зміна частки негативних коментарів порівняно з весною, в процентних пунктах")
    note(fig, "Смуга: середні 90% зі 116 шестиденних вікон весни (2.03–30.06), кожне порівняно з рештою весни.\n"
              "Довірчий інтервал: бутстреп по днях.\n"
              "Тональність: xlm-roberta, ще не звірена з ручною розміткою.")
    return save(fig, "h2_tone")


def fedorov():
    gap = pd.read_csv(H2 / "gap.csv")
    gap = gap[gap.definition == "body"]
    fig = plt.figure(figsize=(WIDTH, 3.25))
    ax = fig.add_axes([0.25, 0.25, 0.70, 0.46])
    for g in GROUPS:
        for p, dy, col, face, bold in [("baseline", 0.17, CONTEXT, SURFACE, False), ("window", -0.17, MARKER, MARKER, True)]:
            r = gap[(gap.group == g) & (gap.period == p)].iloc[0]
            y = ROW[g] + dy
            ax.hlines(y, r.gap_std_lo * 100, r.gap_std_hi * 100, color=col, lw=1.8 if bold else 1.3)
            ax.plot(r.gap_std * 100, y, "o", color=col, mfc=face, ms=6.5 if bold else 5.5, mew=1.4, zorder=3)
            ax.text(r.gap_std_hi * 100 + 1.2, y, num(r.gap_std * 100, 0), va="center", ha="left",
                    fontsize=9 if bold else 8.5, color=INK if bold else INK2,
                    fontproperties=BOLD if bold else None)
    # legend above the plot, so it reads as applying to every row
    ax.plot(-18.5, 2.72, "o", color=CONTEXT, mfc=SURFACE, ms=5.5, mew=1.4, clip_on=False)
    ax.text(-17, 2.72, "березень–червень", ha="left", va="center", fontsize=7.5, color=INK2)
    ax.plot(-1.5, 2.72, "o", color=MARKER, ms=6, clip_on=False)
    ax.text(0, 2.72, "15–20.07", ha="left", va="center", fontsize=7.5, color=INK2)
    ax.text(9.5, 2.72, "лінія: 95% довірчий інтервал", ha="left", va="center", fontsize=7.5, color=INK2)
    ax.axvline(0, color=INK2, lw=0.8, zorder=0)
    ax.text(0.8, -0.62, "0: так само негативні, як інші коментарі", ha="left", va="center", fontsize=7.5, color=INK2)
    ax.set_yticks([ROW[g] for g in GROUPS], [GROUP_LABELS[g] for g in GROUPS])
    ax.set_ylim(-0.8, 2.5)
    ax.set_xlim(-20, 44)
    ticks = [-20, -10, 0, 10, 20, 30, 40]
    ax.set_xticks(ticks, [num(t, 0) if t else "0" for t in ticks[:-1]] + [f"{num(40, 0)} {PP}"])
    header(fig, FIG_NO["fedorov"], "Англомовні спільноти пишуть про Федорова негативніше лише з протестів",
           "Частка негативних серед коментарів зі згадкою Федорова мінус частка серед інших, в процентних пунктах",
           size=12)
    note(fig, "Інші коментарі тих самих сабредітів перезважено під склад коментарів про Федорова.\n"
              "Довірчий інтервал: бутстреп по тредах. Навесні коментарів зі згадкою Федорова лише 43–70 на групу.\n"
              "Тональність: xlm-roberta, ще не звірена з ручною розміткою.")
    return save(fig, "h2_fedorov")


if __name__ == "__main__":
    apply()
    for f in (tone, fedorov):
        print(f())
