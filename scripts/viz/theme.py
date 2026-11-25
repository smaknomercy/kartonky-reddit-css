"""
One look for every HW4 chart (chosen 25.11 from variants A/B/C, see ai/chart_review_*.md).

Rules
- one chart = one message, at most ~6 labelled numbers; the rest goes to the note or the text
- one font for everything: PT Sans (the report body uses it too). A handwritten title font was tried
  (Caveat, Pangolin, Neucha, Amatic SC) and dropped on 25.11: it read as a sticker on a clean chart.
  The cardboard look comes from the kraft surface, the red marker and, on 1-2 key charts, a real
  poster slogan quoted in PT Sans
- flat kraft surface
- red (MARKER) means "the protest days" in every chart; spring / ordinary weeks are gray (CONTEXT)
- group colours (HW3 families, deepened for kraft) only where a group is a series (lines);
  text is always in ink, never in the series colour
- Ukrainian only, decimal comma, real minus, "в. п." with a non-breaking space, no em dashes

Palette checked with the dataviz validator on #f4ecdc, all pairs incl. MARKER:
CVD dE 10.8, normal dE 18.5. Orange is 2.84:1 on kraft, so group lines always carry direct labels.
Fonts: assets/fonts (SIL OFL).
"""
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm

ROOT = Path(__file__).resolve().parents[2]
FONTS = ROOT / "assets" / "fonts"
FIG_DIR = ROOT / "reports" / "figures" / "hw4"

SURFACE = "#f4ecdc"   # kraft paper
BAND = "#e7dac0"      # spring range, event windows
INK = "#2b2622"
INK2 = "#6b6259"
GRID = "#e2d6bf"
MARKER = "#b3192b"    # protest days
CONTEXT = "#a39a8e"   # spring, ordinary weeks

GROUPS = ["ua", "ua_en", "intl"]
GROUP_COLORS = {"ua": "#2166c4", "ua_en": "#d9701e", "intl": "#0f9a7c"}
GROUP_LABELS = {"ua": "українські спільноти", "ua_en": "англомовні про Україну", "intl": "міжнародні"}

WIDTH = 6.3           # inches: A4 text width
NB = " "
PP = f"в.{NB}п."
MINUS = "−"

BOLD = fm.FontProperties(fname=FONTS / "PT_Sans-Web-Bold.ttf")


def apply():
    fm.fontManager.addfont(str(FONTS / "PT_Sans-Web-Regular.ttf"))
    mpl.rcParams.update({
        "font.family": "PT Sans", "font.size": 8,
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "figure.dpi": 100, "savefig.dpi": 300,
        "axes.edgecolor": INK2, "axes.labelcolor": INK2, "axes.labelsize": 8,
        "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
        "axes.grid": False, "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
        "xtick.color": INK2, "ytick.color": INK, "xtick.labelsize": 8, "ytick.labelsize": 9,
        "xtick.major.size": 2.5, "xtick.major.width": 0.6, "ytick.major.size": 0, "axes.linewidth": 0.6,
        "lines.solid_capstyle": "round", "text.color": INK, "axes.unicode_minus": True,
    })


def num(v, dec=1, sign=True):
    """Decimal comma, real minus sign, optional plus."""
    s = f"{v:+.{dec}f}" if sign else f"{v:.{dec}f}"
    return s.replace("-", MINUS).replace(".", ",")


def header(fig, n, title, sub=None, top_in=0.3, size=13):
    """'Рис. N ·' in grey, the finding in ink, both PT Sans bold; a one-line subtitle under it."""
    H = fig.get_figheight()
    y = 1 - top_in / H
    lab = fig.text(0.012, y, f"Рис.{NB}{n} ·", fontproperties=BOLD, fontsize=9.5, color=INK2,
                   va="baseline", ha="left")
    fig.canvas.draw()
    bb = lab.get_window_extent().transformed(fig.transFigure.inverted())
    fig.text(bb.x1 + 0.008, y, title, fontproperties=BOLD, fontsize=size, color=INK, va="baseline", ha="left")
    if sub:
        fig.text(0.012, y - 0.27 / H, sub, fontsize=8.5, color=INK2, va="baseline", ha="left")


def note(fig, text, bottom_in=0.1):
    fig.text(0.012, bottom_in / fig.get_figheight(), text, fontsize=7.5, color=INK2, va="bottom", ha="left",
             linespacing=1.35)


def save(fig, name):
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    path = FIG_DIR / f"{name}.png"
    fig.savefig(path)
    plt.close(fig)
    return path
