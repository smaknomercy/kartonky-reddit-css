"""
One look for every HW4 chart: clean charts with "cardboard" accents.

- kraft-beige surface; a handwritten font (Caveat, Cyrillic) only in titles; PT Sans everywhere else
- one marker colour for the main series (MARKER); context series in gray (CONTEXT)
- one palette for the subreddit groups, used in the same order in every chart (GROUP_COLORS).
  Checked with the dataviz palette validator on this surface, all pairs: CVD dE 12.6, normal 23.0.
  Orange and aqua are below 3:1 contrast on the beige, so group lines always carry direct labels.
- titles state the finding; the subtitle says what is plotted and how; the source line sits at the bottom

Fonts are in assets/fonts (SIL OFL). Caveat-Bold.ttf is a static 700 instance of the variable font.

Usage:
    from theme import apply, title, source, save, GROUP_COLORS, GROUP_LABELS, MARKER, CONTEXT
    apply()
    fig, ax = plt.subplots()
    ...
    title(fig, "Finding as a sentence", "What is plotted, units, weighting")
    source(fig, "Reddit, 8 subreddits; xlm-roberta sentiment")
    save(fig, "h2_tone_daily")
"""
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
from matplotlib.ticker import FuncFormatter

ROOT = Path(__file__).resolve().parents[2]
FONTS = ROOT / "assets" / "fonts"
FIG_DIR = ROOT / "reports" / "figures" / "hw4"

SURFACE = "#f4ecdc"   # kraft paper
SHADE = "#e6d8bd"     # event windows
INK = "#2b2622"       # titles, labels, values
INK2 = "#6b6259"      # axes, subtitles, notes
GRID = "#e0d3ba"
MARKER = "#e0393e"    # the one highlight: main series
CONTEXT = "#a39a8e"   # comparison / background series

GROUPS = ["ua", "ua_en", "intl"]
GROUP_COLORS = {"ua": "#257de0", "ua_en": "#f47230", "intl": "#00b894"}
GROUP_LABELS = {"ua": "українські спільноти", "ua_en": "англомовні про Україну", "intl": "міжнародні"}

WIDTH = 6.3  # inches: A4 text width with 2.5 cm margins

TITLE_FONT = fm.FontProperties(fname=FONTS / "Caveat-Bold.ttf")
BOLD = fm.FontProperties(fname=FONTS / "PT_Sans-Web-Bold.ttf")


def apply():
    fm.fontManager.addfont(str(FONTS / "PT_Sans-Web-Regular.ttf"))
    mpl.rcParams.update({
        "font.family": "PT Sans", "font.size": 9.5,
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "figure.figsize": (WIDTH, 3.6), "figure.dpi": 110, "savefig.dpi": 300,
        "axes.edgecolor": INK2, "axes.labelcolor": INK2, "axes.labelsize": 9,
        "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
        "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID, "grid.linewidth": 0.8,
        "axes.axisbelow": True, "axes.titlesize": 10, "axes.titlecolor": INK, "axes.titlelocation": "left",
        "xtick.color": INK2, "ytick.color": INK2, "xtick.labelsize": 8.5, "ytick.labelsize": 8.5,
        "ytick.left": False, "xtick.major.size": 3,
        "lines.linewidth": 2, "lines.solid_capstyle": "round", "lines.markersize": 6,
        "legend.frameon": False, "legend.fontsize": 8.5, "legend.labelcolor": INK,
        "text.color": INK, "axes.prop_cycle": mpl.cycler(color=[GROUP_COLORS[g] for g in GROUPS]),
    })


def title(fig, text, sub=None, y=0.985):
    """Handwritten title (the finding) and a plain subtitle, left-aligned to the figure."""
    fig.text(0.012, y, text, fontproperties=TITLE_FONT, fontsize=19, color=INK, va="top", ha="left")
    if sub:
        fig.text(0.012, y - 0.085, sub, fontsize=9, color=INK2, va="top", ha="left")


def source(fig, text):
    fig.text(0.012, 0.012, text, fontsize=7.5, color=INK2, va="bottom", ha="left")


def pct(ax, axis="y", decimals=0):
    f = FuncFormatter(lambda v, _: f"{v * 100:.{decimals}f}%".replace(".", ","))
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(f)


def mark_period(ax, start, end, label=None, y=0.98):
    """Shaded event window with its label written on the chart."""
    ax.axvspan(start, end, color=SHADE, zorder=0, lw=0)
    if label:
        ax.text(start, y, " " + label, transform=ax.get_xaxis_transform(), fontsize=8, color=INK2,
                va="top", ha="left")


def end_label(ax, x, y, text, color, dx=4, dy=0, bold=False):
    """Direct label at the end of a line, in ink (identity comes from the dot of the series colour)."""
    ax.plot([x], [y], "o", ms=5, color=color, mec=SURFACE, mew=1.5, zorder=5, clip_on=False)
    ax.annotate(text, (x, y), xytext=(dx, dy), textcoords="offset points", va="center", ha="left",
                fontsize=8.5, color=INK, fontproperties=BOLD if bold else None, annotation_clip=False)


def save(fig, name, top=0.80, bottom=0.12, left=0.07, right=0.97):
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    fig.subplots_adjust(top=top, bottom=bottom, left=left, right=right)
    path = FIG_DIR / f"{name}.png"
    fig.savefig(path)
    plt.close(fig)
    return path
