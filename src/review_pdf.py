"""Render the weekly review (see data/review.py) to a PDF, using matplotlib.

matplotlib is already a dependency (the dashboard charts use it), so this adds no
new package to the locked-down server. We draw a letter-size, multi-page document
by hand: a flowing y-cursor lays down the title block, the numbered top-N table,
then one block per staff member, starting a fresh page whenever the next row
would run past the bottom margin. Output is raw PDF bytes, ready to attach to an
email or stream as a download.

Brand: the firm's letterhead navy + green, Times serif — matching the on-screen
charts (webapp._workload_chart) so the emailed review looks of a piece with the
dashboard.
"""
from __future__ import annotations

import io

import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["font.family"] = ["Times New Roman", "Times", "serif"]
import matplotlib.patches  # noqa: E402  (Rectangle for zebra striping)
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure

# Letterhead palette (shared with the dashboard charts).
NAVY = "#1a3f8f"
GREEN = "#4a9d4f"
INK = "#16202e"
MUTED = "#55606f"
RULE = "#d9d2c4"
BAND = "#eef1f7"  # pale navy tint for zebra striping

PAGE_W, PAGE_H = 8.5, 11.0  # inches, US Letter portrait

# All layout is in figure-fraction coordinates (0..1). The cursor `y` is measured
# from the TOP of the page (0 = top edge, 1 = bottom edge): it starts at START_Y
# and grows downward; nothing is drawn past MAX_Y (leaving the bottom margin).
LEFT, RIGHT = 0.08, 0.92
START_Y = 0.07      # top margin
MAX_Y = 0.93        # 1.0 - bottom margin
LINE = 0.021        # one body row
GAP = 0.012


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

    def _yc(self) -> float:
        """Convert the top-down cursor into matplotlib's bottom-up y."""
        return 1.0 - self.y

    def ensure(self, need: float):
        """Start a new page if `need` fraction of height won't fit below cursor."""
        if self.y + need > MAX_Y:
            self._new_page()

    def text(self, x, s, size=10.5, weight="normal", color=INK, ha="left"):
        self.ax.text(x, self._yc(), s, fontsize=size, fontweight=weight,
                     color=color, ha=ha, va="top")

    def rule(self, color=RULE, width=1.0):
        yc = self._yc()
        self.ax.plot([LEFT, RIGHT], [yc, yc], color=color, linewidth=width,
                     solid_capstyle="butt")

    def band(self, height):
        """Draw a pale zebra band starting at the cursor, `height` tall."""
        yc_top = self._yc()
        self.ax.add_patch(matplotlib.patches.Rectangle(
            (LEFT - 0.005, yc_top - height), (RIGHT - LEFT) + 0.01, height,
            facecolor=BAND, edgecolor="none", zorder=0))

    def advance(self, dy):
        self.y += dy

    def close(self):
        if self.fig is not None:
            self.pdf.savefig(self.fig, facecolor="white")
            self.fig.clf()
            self.fig = None


# Column x-positions for the statements table. The firm-wide Top-N uses the full
# set (with an EMPLOYEE column); each per-staff table reuses the SAME format minus
# that column (the employee is the section heading), with client/doc widened.
_TOP_COLS = {
    "rank":   (0.095, "right"),
    "client": (0.120, "left"),
    "doc":    (0.395, "left"),
    "emp":    (0.660, "left"),
    "days":   (0.905, "right"),
}
_STAFF_COLS = {
    "rank":   (0.115, "right"),
    "client": (0.140, "left"),
    "doc":    (0.480, "left"),
    "days":   (0.905, "right"),
}


def _fmt_date(dt) -> str:
    months = ("January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December")
    try:
        hour12 = dt.hour % 12 or 12
        mer = "AM" if dt.hour < 12 else "PM"
        return (f"{months[dt.month - 1]} {dt.day}, {dt.year} "
                f"at {hour12}:{dt.minute:02d} {mer}")
    except AttributeError:
        return str(dt)


_MONTHS_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
                "Oct", "Nov", "Dec")


def _short_date(iso: str) -> str:
    """'2026-07-20' -> 'Jul 20'."""
    try:
        y, m, d = str(iso)[:10].split("-")
        return f"{_MONTHS_ABBR[int(m) - 1]} {int(d)}"
    except (ValueError, IndexError):
        return str(iso or "")


# Columns for the "Completed this week" table.
_DONE_COLS = {
    "client": (0.120, "left"),
    "type":   (0.440, "left"),
    "emp":    (0.700, "left"),
    "date":   (0.905, "right"),
}


def _table_header(doc: _Doc, cols: dict, show_emp: bool):
    doc.text(cols["rank"][0], "#", size=9.5, weight="bold", color=MUTED, ha="right")
    doc.text(cols["client"][0], "CLIENT", size=9.5, weight="bold", color=MUTED)
    doc.text(cols["doc"][0], "DOCUMENT / WORK", size=9.5, weight="bold", color=MUTED)
    if show_emp:
        doc.text(cols["emp"][0], "EMPLOYEE", size=9.5, weight="bold", color=MUTED)
    doc.text(cols["days"][0], "DAYS OVERDUE", size=9.5, weight="bold",
             color=MUTED, ha="right")
    doc.advance(LINE * 0.9)
    doc.rule(color=NAVY, width=1.3)
    doc.advance(GAP)


def _table_row(doc: _Doc, cols: dict, r: dict, show_emp: bool, striped: bool):
    if striped:
        doc.band(LINE)
    doc.text(cols["rank"][0], str(r["rank"]), weight="bold", color=NAVY, ha="right")
    doc.text(cols["client"][0], _truncate(r["client"], 34 if show_emp else 42))
    doc.text(cols["doc"][0], _truncate(r["title"], 30 if show_emp else 46), color=MUTED)
    if show_emp:
        doc.text(cols["emp"][0], _truncate(r["assignee"], 22), color=MUTED)
    doc.text(cols["days"][0], str(r["days_overdue"]), weight="bold", ha="right")
    doc.advance(LINE)


# Approximate width of one character as a fraction of page width, for Times ~10pt.
# Deliberately a touch generous so wrapping errs toward extra space, never overlap.
_PER_CHAR = 0.0086


def _flow_type_totals(doc: _Doc, types: list, indent: float):
    """Lay the "<type> <count>" pairs out left-to-right, wrapping to a new line
    when the next item would pass the right margin. The count is bold navy so it
    reads as the value and visually separates one item from the next."""
    gap = 0.022               # space between items
    x = indent
    doc.ensure(LINE)
    for t in types:
        label = str(t["type"])
        count = str(t["count"])
        label_w = len(label) * _PER_CHAR
        item_w = label_w + _PER_CHAR + len(count) * _PER_CHAR
        if x > indent and x + item_w > RIGHT:   # wrap
            doc.advance(LINE)
            doc.ensure(LINE)
            x = indent
        doc.text(x, label, size=10, color=INK)
        doc.text(x + label_w + _PER_CHAR, count, size=10, weight="bold", color=NAVY)
        x += item_w + gap
    doc.advance(LINE)


def render_pdf(rv: dict) -> bytes:
    """Render a review dict (from review.build_review) to PDF bytes."""
    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        doc = _Doc(pdf)

        # ---- Title block --------------------------------------------------
        doc.text(LEFT, rv["firm_name"], size=27, weight="bold", color=NAVY)
        doc.advance(0.045)
        doc.text(LEFT, "Weekly Review", size=15, weight="bold", color=GREEN)
        doc.advance(0.03)
        doc.text(LEFT, f"Generated {_fmt_date(rv['generated_at'])}",
                 size=10, color=MUTED)
        doc.advance(0.02)
        doc.text(LEFT, f"{rv['total_overdue']} overdue statement(s) firm-wide",
                 size=10, color=MUTED)
        doc.advance(0.028)
        doc.rule(color=GREEN, width=2.0)
        doc.advance(0.03)

        # ---- Completed this week (top of the report) ----------------------
        done = rv.get("completed_this_week", [])
        doc.text(LEFT, "Completed this week", size=15, weight="bold", color=INK)
        doc.text(RIGHT, f"since {_short_date(rv.get('week_start', ''))}", size=10,
                 color=MUTED, ha="right")
        doc.advance(0.032)
        if done:
            doc.text(_DONE_COLS["client"][0], "CLIENT", size=9.5, weight="bold", color=MUTED)
            doc.text(_DONE_COLS["type"][0], "RETURN TYPE", size=9.5, weight="bold", color=MUTED)
            doc.text(_DONE_COLS["emp"][0], "EMPLOYEE", size=9.5, weight="bold", color=MUTED)
            doc.text(_DONE_COLS["date"][0], "COMPLETED", size=9.5, weight="bold",
                     color=MUTED, ha="right")
            doc.advance(LINE * 0.9)
            doc.rule(color=GREEN, width=1.3)
            doc.advance(GAP)
            for i, r in enumerate(done):
                doc.ensure(LINE)
                if i % 2 == 1:
                    doc.band(LINE)
                doc.text(_DONE_COLS["client"][0], _truncate(r["client"], 34), weight="bold")
                doc.text(_DONE_COLS["type"][0], _truncate(r["return_type"], 26), color=MUTED)
                doc.text(_DONE_COLS["emp"][0], _truncate(r["assignee"], 20), color=MUTED)
                doc.text(_DONE_COLS["date"][0], _short_date(r["completed_at"]),
                         weight="bold", color=GREEN, ha="right")
                doc.advance(LINE)
        else:
            doc.text(LEFT, "No returns marked completed yet this week.",
                     size=11, color=MUTED)
            doc.advance(LINE)
        doc.advance(0.03)

        # ---- Top N statements ---------------------------------------------
        doc.text(LEFT, f"Top {rv['top_n']} most overdue statements", size=15,
                 weight="bold", color=INK)
        doc.advance(0.032)
        if rv["top"]:
            _table_header(doc, _TOP_COLS, show_emp=True)
            for i, r in enumerate(rv["top"]):
                doc.ensure(LINE)
                _table_row(doc, _TOP_COLS, r, show_emp=True, striped=(i % 2 == 1))
        else:
            doc.text(LEFT, "Nothing overdue — every statement is on track.",
                     size=11, color=MUTED)
            doc.advance(LINE)
        doc.advance(0.03)

        # ---- Per staff: a statements table (same format as the Top N, minus the
        #      employee column) followed by that person's counts by return type. --
        doc.ensure(0.09)
        doc.text(LEFT, "Overdue statements by staff member", size=15,
                 weight="bold", color=INK)
        doc.advance(0.034)

        if not rv["per_staff"]:
            doc.text(LEFT, "No staff member has an overdue statement.",
                     size=11, color=MUTED)
        for staff in rv["per_staff"]:
            # Keep the heading with the table header + at least one row on the page.
            doc.ensure(0.032 + LINE * 3)
            doc.text(LEFT, staff["assignee"], size=13, weight="bold", color=NAVY)
            # Prominent total: a big count with a small "overdue" label to its left.
            cnt = str(staff["count"])
            num_w = len(cnt) * _PER_CHAR * 1.9   # ~width of the count at size 19
            doc.text(RIGHT, cnt, size=19, weight="bold", color=NAVY, ha="right")
            doc.text(RIGHT - num_w - 0.006, "overdue", size=9.5, color=MUTED, ha="right")
            doc.advance(0.034)

            _table_header(doc, _STAFF_COLS, show_emp=False)
            for i, r in enumerate(staff["statements"]):
                doc.ensure(LINE)
                _table_row(doc, _STAFF_COLS, r, show_emp=False, striped=(i % 2 == 1))

            # Totals by return type, under the table, laid out HORIZONTALLY as
            # "<type> <count>" items that flow left-to-right and wrap. Counts cover
            # ALL of this person's overdue statements, not just the rows shown above.
            doc.advance(0.018)
            doc.ensure(LINE * 2)
            doc.text(_STAFF_COLS["client"][0], "Total by return type", size=9.5,
                     weight="bold", color=MUTED)
            doc.advance(LINE)
            _flow_type_totals(doc, staff["types"], indent=_STAFF_COLS["client"][0])
            doc.advance(0.03)

        doc.close()
    buf.seek(0)
    return buf.read()
