"""Render the weekly review (see data/review.py) to a PDF, using matplotlib.

matplotlib is already a dependency (the dashboard charts use it), so this adds no
new package to the locked-down server. The document is LANDSCAPE US-letter and
mirrors the on-screen Weekly Review (templates/review.html + review_staff.html):

  1. A summary page: title, "Completed this week" (capped to the 15 most recent,
     matching the page), then "Overdue by staff member" — each staff member with
     their overdue-return total and a breakdown by return type.
  2. One page PER staff member: three headline tiles (completed this week / open /
     overdue returns), an "open returns by work type" bar breakdown, then their
     Top-10 most overdue statements and 10 most recent statements side by side.

We draw by hand with a flowing top-down y-cursor (see _Doc); the two staff-page
lists are drawn side by side by resetting the cursor between the columns. Output
is raw PDF bytes, ready to stream inline or as a download.

Brand: the firm's letterhead navy + green, Times serif — matching the on-screen
charts so the printout looks of a piece with the dashboard.
"""
from __future__ import annotations

import io

import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["font.family"] = ["Times New Roman", "serif"]
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

# Letterhead palette (shared with the dashboard charts).
NAVY = "#1a3f8f"
GREEN = "#4a9d4f"
GREEN_INK = "#3d7a42"
INK = "#16202e"
MUTED = "#55606f"
RULE = "#d9d2c4"
BAND = "#eef1f7"   # pale navy tint for zebra striping / bar tracks
DANGER = "#b91c1c"

PAGE_W, PAGE_H = 11.0, 8.5  # inches, US Letter LANDSCAPE

# Layout in figure-fraction coordinates (0..1). The cursor `y` is measured from the
# TOP of the page (0 = top edge) and grows downward; nothing is drawn past MAX_Y.
LEFT, RIGHT = 0.05, 0.95
START_Y = 0.06
MAX_Y = 0.94
LINE = 0.028        # one body row (fraction of the SHORTER landscape page)
GAP = 0.015


def _truncate(s: str, n: int) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1] + "…"


class _Doc:
    """A paginating canvas: draw top-to-bottom; new pages open automatically."""

    def __init__(self, pdf: PdfPages):
        self.pdf = pdf
        self.fig = None
        self.ax = None
        self.y = 0.0
        self._new_page()

    def _new_page(self):
        if self.fig is not None:
            self.pdf.savefig(self.fig, facecolor="white")
            self.fig.clf()
        self.fig = Figure(figsize=(PAGE_W, PAGE_H), facecolor="white")
        self.ax = self.fig.add_axes([0, 0, 1, 1])  # full page
        self.ax.set_xlim(0, 1)
        self.ax.set_ylim(0, 1)
        self.ax.axis("off")
        self.y = START_Y

    def new_page(self):
        """Force a fresh page (used to start each staff member on their own page)."""
        self._new_page()

    def _yc(self) -> float:
        """Convert the top-down cursor into matplotlib's bottom-up y."""
        return 1.0 - self.y

    def ensure(self, need: float):
        """Start a new page if `need` fraction of height won't fit below cursor."""
        if self.y + need > MAX_Y:
            self._new_page()

    def text(self, x, s, size=10.5, weight="normal", color=INK, ha="left",
             y=None, va="top"):
        yc = (1.0 - y) if y is not None else self._yc()
        self.ax.text(x, yc, s, fontsize=size, fontweight=weight,
                     color=color, ha=ha, va=va)

    def rule(self, color=RULE, width=1.0, x0=LEFT, x1=RIGHT):
        yc = self._yc()
        self.ax.plot([x0, x1], [yc, yc], color=color, linewidth=width,
                     solid_capstyle="butt")

    def vline(self, x, y_top, y_bottom, color=RULE, width=1.0):
        """Vertical rule at `x` from `y_top` down to `y_bottom` (top-down
        fractions). Draws on THIS CALL's current page — a page's axes are
        flushed to the PDF the instant a new page starts, so a span that might
        cross a page break must be drawn incrementally (e.g. once per row),
        never computed once and drawn after the fact."""
        self.ax.plot([x, x], [1.0 - y_top, 1.0 - y_bottom], color=color,
                     linewidth=width, solid_capstyle="butt", zorder=2)

    def band(self, height, x0=LEFT, x1=RIGHT):
        """Draw a pale zebra band starting at the cursor, `height` tall."""
        yc_top = self._yc()
        self.ax.add_patch(Rectangle((x0 - 0.004, yc_top - height),
                                    (x1 - x0) + 0.008, height,
                                    facecolor=BAND, edgecolor="none", zorder=0))

    def advance(self, dy):
        self.y += dy

    def close(self):
        if self.fig is not None:
            self.pdf.savefig(self.fig, facecolor="white")
            self.fig.clf()
            self.fig = None


_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")
_MONTHS_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
                "Oct", "Nov", "Dec")


def _fmt_date(dt) -> str:
    try:
        hour12 = dt.hour % 12 or 12
        mer = "AM" if dt.hour < 12 else "PM"
        return (f"{_MONTHS[dt.month - 1]} {dt.day}, {dt.year} "
                f"at {hour12}:{dt.minute:02d} {mer}")
    except AttributeError:
        return str(dt)


def _short_date(iso: str) -> str:
    """'2026-07-20' -> 'Jul 20'."""
    try:
        y, m, d = str(iso)[:10].split("-")
        return f"{_MONTHS_ABBR[int(m) - 1]} {int(d)}"
    except (ValueError, IndexError):
        return str(iso or "")


# Approximate width of one character as a fraction of page width, for Times ~10pt
# on this landscape page. A touch generous so wrapping errs toward extra space.
_PER_CHAR = 0.0062


def _type_chip_row(doc: _Doc, types: list, indent: float, x_right: float = RIGHT):
    """Lay out "<type> <count>" as bounded CHIP pills, left-to-right, wrapping at
    x_right — the PDF equivalent of the on-screen `.type-chip` (a light tag
    background, muted type name, bold navy count), so a type + its count read as
    one discrete unit instead of a run of plain text."""
    chip_h = 0.026
    pad_x = 0.009
    inner_gap = 0.006     # between the type name and its count, inside the chip
    gap = 0.012           # between chips
    x = indent
    doc.ensure(chip_h + 0.006)
    for t in types:
        label = str(t["type"])
        count = str(t["count"])
        label_w = len(label) * _PER_CHAR
        count_w = len(count) * _PER_CHAR
        item_w = pad_x * 2 + label_w + inner_gap + count_w
        if x > indent and x + item_w > x_right:       # wrap to a new chip row
            doc.advance(chip_h + 0.010)
            doc.ensure(chip_h + 0.006)
            x = indent
        top = doc._yc()
        mid = top - chip_h / 2
        doc.ax.add_patch(Rectangle((x, top - chip_h), item_w, chip_h,
                                   facecolor="#f3f0e9", edgecolor=RULE,
                                   linewidth=0.8, zorder=1))
        doc.ax.text(x + pad_x, mid, label, fontsize=8.5, color=MUTED,
                    ha="left", va="center", zorder=2)
        doc.ax.text(x + pad_x + label_w + inner_gap, mid, count, fontsize=9.5,
                    fontweight="bold", color=NAVY, ha="left", va="center", zorder=2)
        x += item_w + gap
    doc.advance(chip_h + 0.010)


# ---- Statement tables (used on each staff page, drawn in a half-width column) ---

# Row pitch for the staff-page statement tables — tighter than a summary LINE so
# ten rows plus the tiles and bar breakdown all fit on one landscape page.
_ROW = 0.026


def _stmt_cols(x0: float, x1: float, show_emp: bool = False) -> dict:
    """Column x-positions AND per-column character budgets for a statements table
    spanning [x0, x1]. The char budgets reserve room for the right-aligned last
    value so a long client/title truncates instead of colliding with it. With
    show_emp an EMPLOYEE column is inserted before the last column (used on the
    full-width firm-wide tables, which span staff)."""
    w = x1 - x0
    last_reserve = 0.05          # space kept clear before the right-aligned value
    if show_emp:
        client_x = x0 + 0.022
        doc_x = x0 + 0.022 + w * 0.26
        emp_x = x0 + 0.022 + w * 0.60
        return {
            "rank":   x0 + 0.010,
            "client": client_x,
            "doc":    doc_x,
            "emp":    emp_x,
            "last":   x1,
            "show_emp": True,
            "client_chars": max(8, int((doc_x - client_x - 0.006) / _PER_CHAR)),
            "doc_chars":    max(8, int((emp_x - doc_x - 0.006) / _PER_CHAR)),
            "emp_chars":    max(6, int((x1 - last_reserve - emp_x) / _PER_CHAR)),
        }
    client_x = x0 + 0.026
    doc_x = x0 + 0.026 + w * 0.40
    return {
        "rank":   x0 + 0.014,
        "client": client_x,
        "doc":    doc_x,
        "last":   x1,
        "show_emp": False,
        "client_chars": max(8, int((doc_x - client_x - 0.006) / _PER_CHAR)),
        "doc_chars":    max(8, int((x1 - last_reserve - doc_x) / _PER_CHAR)),
    }


def _stmt_header(doc: _Doc, cols: dict, last_label: str):
    doc.text(cols["rank"], "#", size=8.5, weight="bold", color=MUTED, ha="right")
    doc.text(cols["client"], "CLIENT", size=8.5, weight="bold", color=MUTED)
    doc.text(cols["doc"], "DOCUMENT / WORK", size=8.5, weight="bold", color=MUTED)
    if cols.get("show_emp"):
        doc.text(cols["emp"], "EMPLOYEE", size=8.5, weight="bold", color=MUTED)
    doc.text(cols["last"], last_label, size=8.5, weight="bold", color=MUTED, ha="right")
    doc.advance(_ROW * 0.85)
    doc.rule(color=NAVY, width=1.2, x0=cols["rank"] - 0.012, x1=cols["last"])
    doc.advance(GAP * 0.7)


def _stmt_row(doc: _Doc, cols: dict, r: dict, last_val: str, striped: bool):
    base = doc.y
    if striped:
        doc.band(_ROW, x0=cols["rank"] - 0.012, x1=cols["last"])
    cy = base + _ROW * 0.5          # vertical center of the row, so text sits inside the band
    doc.text(cols["rank"], str(r["rank"]), size=9.5, weight="bold", color=NAVY, ha="right", y=cy, va="center")
    doc.text(cols["client"], _truncate(r["client"], cols["client_chars"]), size=9.5, y=cy, va="center")
    doc.text(cols["doc"], _truncate(r["title"], cols["doc_chars"]), size=9.5, color=MUTED, y=cy, va="center")
    if cols.get("show_emp"):
        doc.text(cols["emp"], _truncate(r.get("assignee", ""), cols["emp_chars"]),
                 size=9.5, color=MUTED, y=cy, va="center")
    doc.text(cols["last"], str(last_val), size=9.5, weight="bold", ha="right", y=cy, va="center")
    doc.advance(_ROW)


def _stmt_table(doc: _Doc, x0: float, x1: float, start_y: float, title: str,
                rows: list, last_label: str, last_key, empty_msg: str,
                show_emp: bool = False) -> float:
    """Draw a titled statements table in the column [x0, x1] starting at start_y.
    Returns the cursor y at the end (so the caller can align/stack tables)."""
    doc.y = start_y
    doc.text(x0, title, size=11.5, weight="bold", color=INK)
    doc.advance(_ROW * 1.15)
    cols = _stmt_cols(x0, x1, show_emp=show_emp)
    _stmt_header(doc, cols, last_label)
    if rows:
        for i, r in enumerate(rows):
            _stmt_row(doc, cols, r, last_key(r), striped=(i % 2 == 1))
    else:
        doc.text(x0, empty_msg, size=10, color=MUTED)
        doc.advance(LINE)
    return doc.y


# ---- Staff-page headline tiles + type-bar breakdown ---------------------------

def _tiles(doc: _Doc, tiles: list):
    """Three headline tiles across the width: (value, label, color)."""
    n = len(tiles)
    gap = 0.02
    w = (RIGHT - LEFT - gap * (n - 1)) / n
    h = 0.12
    top = doc._yc()
    for i, (val, label, color, alert) in enumerate(tiles):
        x = LEFT + i * (w + gap)
        doc.ax.add_patch(Rectangle((x, top - h), w, h, facecolor="white",
                                   edgecolor=RULE, linewidth=1.1, zorder=1))
        if alert:   # red ledger-marker down the leading edge, like the on-screen card
            doc.ax.add_patch(Rectangle((x, top - h), 0.004, h, facecolor=DANGER,
                                       edgecolor="none", zorder=2))
        doc.ax.text(x + 0.018, top - 0.044, str(val), fontsize=28, fontweight="bold",
                    color=color, ha="left", va="center")
        doc.ax.text(x + 0.018, top - 0.094, label, fontsize=9, fontweight="bold",
                    color=MUTED, ha="left", va="center")
    doc.advance(h + 0.025)


def _type_bars(doc: _Doc, types: list, budget: float = 0.21):
    """Horizontal 'open returns by work type' breakdown. Shows EVERY type — no
    "+N more" — by fitting them all into `budget` of page height: the row height
    (and, when it gets small, the font) shrink as the type count grows, so the two
    statement tables below still fit on the one landscape page."""
    if not types:
        doc.text(LEFT, "No open returns.", size=10, color=MUTED)
        doc.advance(LINE)
        return
    n = len(types)
    row_h = min(0.032, budget / n)      # all n rows fit within `budget`
    fs = 9.5 if row_h >= 0.028 else (8.5 if row_h >= 0.022 else 7.5)
    bar_h = min(row_h * 0.55, 0.016)
    top_count = types[0]["count"] or 1
    bar_x0, bar_x1 = LEFT + 0.20, RIGHT - 0.04
    for t in types:
        mid = doc._yc() - row_h * 0.5
        doc.ax.text(LEFT, mid, _truncate(t["type"], 32), fontsize=fs,
                    color=INK, ha="left", va="center")
        doc.ax.add_patch(Rectangle((bar_x0, mid - bar_h / 2), (bar_x1 - bar_x0),
                                   bar_h, facecolor=BAND, edgecolor="none"))
        doc.ax.add_patch(Rectangle((bar_x0, mid - bar_h / 2),
                                   (bar_x1 - bar_x0) * (t["count"] / top_count),
                                   bar_h, facecolor=NAVY, edgecolor="none"))
        doc.ax.text(RIGHT, mid, str(t["count"]), fontsize=fs, fontweight="bold",
                    color=INK, ha="right", va="center")
        doc.advance(row_h)


# ---- Pages --------------------------------------------------------------------

_DONE_CAP = 30   # matches the on-screen "Completed this week" cap (review.html)

_DONE_COLS = {
    "client": LEFT + 0.01,
    "type":   0.40,
    "emp":    0.62,
    "date":   RIGHT,
}


def _summary_page(doc: _Doc, rv: dict):
    # Title block
    doc.text(LEFT, rv["firm_name"], size=26, weight="bold", color=NAVY)
    doc.advance(0.055)
    doc.text(LEFT, "Weekly Review", size=14, weight="bold", color=GREEN)
    doc.advance(0.038)
    doc.text(LEFT, f"Generated {_fmt_date(rv['generated_at'])}", size=9.5, color=MUTED)
    doc.text(RIGHT, f"{rv['total_overdue']} overdue statement(s) firm-wide",
             size=9.5, color=MUTED, ha="right")
    doc.advance(0.028)
    doc.rule(color=GREEN, width=2.0)
    doc.advance(0.04)

    # Completed this week (capped to the 15 most recent, like the page)
    done = rv.get("completed_this_week", [])
    total = len(done)
    doc.text(LEFT, "Completed this week", size=13, weight="bold", color=INK)
    doc.text(RIGHT, f"{total} since {_short_date(rv.get('week_start', ''))}",
             size=9.5, color=MUTED, ha="right")
    doc.advance(0.035)
    if done:
        doc.text(_DONE_COLS["client"], "CLIENT", size=8.5, weight="bold", color=MUTED)
        doc.text(_DONE_COLS["type"], "RETURN TYPE", size=8.5, weight="bold", color=MUTED)
        doc.text(_DONE_COLS["emp"], "EMPLOYEE", size=8.5, weight="bold", color=MUTED)
        doc.text(_DONE_COLS["date"], "COMPLETED", size=8.5, weight="bold", color=MUTED, ha="right")
        doc.advance(LINE * 0.85)
        doc.rule(color=GREEN, width=1.2)
        doc.advance(GAP)
        for i, r in enumerate(done[:_DONE_CAP]):
            doc.ensure(LINE)
            base = doc.y
            if i % 2 == 1:
                doc.band(LINE)
            cy = base + LINE * 0.5      # center the row text within its band
            doc.text(_DONE_COLS["client"], _truncate(r["client"], 44), size=10, weight="bold", y=cy, va="center")
            doc.text(_DONE_COLS["type"], _truncate(r["return_type"], 28), size=10, color=MUTED, y=cy, va="center")
            doc.text(_DONE_COLS["emp"], _truncate(r["assignee"], 30), size=10, color=MUTED, y=cy, va="center")
            doc.text(_DONE_COLS["date"], _short_date(r["completed_at"]), size=10,
                     weight="bold", color=GREEN_INK, ha="right", y=cy, va="center")
            doc.advance(LINE)
        if total > _DONE_CAP:
            doc.advance(GAP)
            doc.text(LEFT, f"Showing the {_DONE_CAP} most recent  ·  +{total - _DONE_CAP} "
                     f"more completed this week.", size=9, color=MUTED)
            doc.advance(LINE)
    else:
        doc.text(LEFT, "No returns marked completed yet this week.", size=10.5, color=MUTED)
        doc.advance(LINE)
    doc.advance(0.03)

    # Overdue by staff member — the three-point summary. Each staff member is
    # one bordered card (NLC navy frame, thin green internal dividers between
    # STAFF MEMBER | OVERDUE | RETURNS — the letterhead's own navy-over-green
    # double-rule signature, reused here). Cards hug their own content (a
    # 1-chip person gets a slim card, a busy person a taller one) and stack
    # with one fixed gap between them, so the rhythm down the page tracks each
    # person's actual content instead of leaving uneven dead space. Name and
    # count are vertically CENTERED within the card's real height for the same
    # reason: a one-line name shouldn't look glued to the top of a tall card.
    DIV1, DIV2 = 0.25, 0.35
    OVERDUE_X, RETURNS_X = 0.325, 0.375
    CARD_GAP = 0.016
    PAD_X, PAD_Y = 0.008, 0.006
    doc.ensure(0.1)
    doc.text(LEFT, "Overdue by staff member", size=13, weight="bold", color=INK)
    doc.advance(0.035)
    doc.text(LEFT, "STAFF MEMBER", size=8.5, weight="bold", color=MUTED)
    doc.text(OVERDUE_X, "OVERDUE", size=8.5, weight="bold", color=MUTED, ha="right")
    doc.text(RETURNS_X, "RETURNS", size=8.5, weight="bold", color=MUTED)
    doc.advance(LINE * 0.85)
    doc.rule(color=NAVY, width=1.2)
    doc.advance(GAP)
    rows = rv.get("staff_rows", [])
    if not rows:
        doc.text(LEFT, "No staff member has Owen-owned work right now.", size=10.5, color=MUTED)
        return
    for s in rows:
        doc.ensure(LINE * 1.6)
        row_top = doc.y
        # Draw the Returns cell first (it decides the card's height); Name and
        # Overdue are placed afterward once that height is known, centered in it.
        if s.get("overdue_by_type"):
            _type_chip_row(doc, s["overdue_by_type"], indent=RETURNS_X, x_right=RIGHT)
        else:
            doc.text(RETURNS_X, "No overdue returns", size=9.5, color=MUTED,
                     y=row_top + LINE * 0.5, va="center")
            doc.advance(LINE)
        content_bottom = doc.y
        mid_y = row_top + (content_bottom - row_top) / 2
        doc.text(LEFT + PAD_X, _truncate(s["assignee"], 26), size=11, weight="bold",
                 color=NAVY, y=mid_y, va="center")
        doc.text(OVERDUE_X, str(s["overdue"]), size=14, weight="bold",
                 color=(DANGER if s["overdue"] else MUTED), ha="right", y=mid_y, va="center")
        box_top, box_bottom = row_top - PAD_Y, content_bottom + PAD_Y
        doc.ax.add_patch(Rectangle((LEFT - PAD_X, 1 - box_bottom),
                                   (RIGHT - LEFT) + 2 * PAD_X, box_bottom - box_top,
                                   facecolor="none", edgecolor=NAVY, linewidth=1.4, zorder=3))
        doc.vline(DIV1, box_top, box_bottom, color=GREEN, width=1.3)
        doc.vline(DIV2, box_top, box_bottom, color=GREEN, width=1.3)
        doc.y = box_bottom + CARD_GAP


def _staff_page(doc: _Doc, sp: dict):
    doc.new_page()
    # Header
    doc.text(LEFT, sp["staff"], size=22, weight="bold", color=NAVY)
    doc.advance(0.055)
    doc.rule(color=NAVY, width=2.0)
    doc.advance(0.006)
    doc.rule(color=GREEN, width=1.2)
    doc.advance(0.03)
    doc.text(LEFT, f"Weekly Review · Owen-owned clients · this week since "
             f"{sp.get('week_start', '')}", size=9.5, color=MUTED)
    doc.advance(0.04)

    # Headline tiles
    _tiles(doc, [
        (sp["completed_week"], "COMPLETED THIS WEEK", GREEN_INK, False),
        (sp["open"], "OPEN RETURNS", NAVY, False),
        (sp["overdue"], "OVERDUE RETURNS", (DANGER if sp["overdue"] else INK), bool(sp["overdue"])),
    ])

    # Open returns by work type
    doc.text(LEFT, "Open returns by work type", size=12, weight="bold", color=INK)
    doc.text(RIGHT, f"{sp['open']} open", size=9.5, color=MUTED, ha="right")
    doc.advance(LINE * 1.1)
    _type_bars(doc, sp.get("open_by_type", []))
    doc.advance(0.02)

    # Two statement lists, side by side
    start_y = doc.y
    left_end = _stmt_table(
        doc, LEFT, 0.475, start_y, "Top 10 most overdue statements",
        sp.get("top_overdue", []), "DAYS OVERDUE", lambda r: r["days_overdue"],
        "Nothing overdue — all caught up.")
    right_end = _stmt_table(
        doc, 0.525, RIGHT, start_y, "10 most recent statements",
        sp.get("recent", []), "OPENED", lambda r: _short_date(r.get("opened", "")),
        "No statements for this staff member.")
    doc.y = max(left_end, right_end)


def _firm_page(doc: _Doc, rv: dict):
    """Firm-wide closing page (after the last staff member): the top-10 most overdue
    statements and the 10 most recent, across everyone. Both carry an Employee
    column since they span staff. Live figures — recomputed from the dashboard on
    every render."""
    doc.new_page()
    doc.text(LEFT, "Firm-wide — most overdue & most recent", size=20, weight="bold", color=NAVY)
    doc.advance(0.05)
    doc.rule(color=NAVY, width=2.0)
    doc.advance(0.006)
    doc.rule(color=GREEN, width=1.2)
    doc.advance(0.03)
    doc.text(LEFT, "Across all staff · Owen-owned clients · live from the dashboard",
             size=9.5, color=MUTED)
    doc.advance(0.04)

    # Full-width, stacked — the Employee column needs the room, and 10+10 rows fit
    # comfortably down one landscape page.
    _stmt_table(doc, LEFT, RIGHT, doc.y, "Top 10 most overdue statements",
                rv.get("top", []), "DAYS OVERDUE", lambda r: r["days_overdue"],
                "Nothing overdue — every statement is on track.", show_emp=True)
    doc.advance(0.03)
    _stmt_table(doc, LEFT, RIGHT, doc.y, "10 most recent statements",
                rv.get("recent", []), "OPENED", lambda r: _short_date(r.get("opened", "")),
                "No statements yet.", show_emp=True)


def render_pdf(rv: dict) -> bytes:
    """Render a review dict (from webapp.build_current_review_pdf) to PDF bytes.

    Expects the build_review keys plus `staff_pages` — a list of build_staff_page
    dicts, one per staff row, for the per-staff detail pages."""
    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        doc = _Doc(pdf)
        _summary_page(doc, rv)
        for sp in rv.get("staff_pages", []):
            _staff_page(doc, sp)
        _firm_page(doc, rv)
        doc.close()
    buf.seek(0)
    return buf.read()
