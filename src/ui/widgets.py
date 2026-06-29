"""Reusable UI building blocks: KPI cards, sortable tables, and a chart canvas."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from ui.theme import COLORS, FONTS


class KPICard(ttk.Frame):
    """A small card showing one headline number with a label."""

    def __init__(self, parent, label: str, accent: str | None = None):
        super().__init__(parent, style="Card.TFrame", padding=(16, 12))
        self.configure(borderwidth=1, relief="solid")
        self._accent = accent or COLORS["accent"]
        self.value_lbl = tk.Label(self, text="-", font=FONTS["kpi"], bg=COLORS["card"], fg=self._accent)
        self.value_lbl.pack(anchor="w")
        self.sub_lbl = tk.Label(self, text=label.upper(), font=FONTS["kpi_label"],
                                bg=COLORS["card"], fg=COLORS["muted"])
        self.sub_lbl.pack(anchor="w")

    def set_value(self, value):
        self.value_lbl.config(text=str(value))


class SortableTable(ttk.Frame):
    """A ttk.Treeview with a scrollbar and click-to-sort column headers.

    `columns` is a list of (key, heading, width, anchor). Rows are dicts keyed by
    column key. A row carrying truthy `_overdue` is highlighted red.
    """

    def __init__(self, parent, columns, height=12):
        super().__init__(parent, style="TFrame")
        self._columns = columns
        keys = [c[0] for c in columns]
        self.tree = ttk.Treeview(self, columns=keys, show="headings", height=height)
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._sort_state: dict[str, bool] = {}
        for key, heading, width, anchor in columns:
            self.tree.heading(key, text=heading, command=lambda k=key: self._sort_by(k))
            self.tree.column(key, width=width, anchor=anchor, stretch=True)

        self.tree.tag_configure("overdue", background=COLORS["danger_bg"], foreground=COLORS["danger"])
        self.tree.tag_configure("odd", background="#fafbfc")
        self._rows: list[dict] = []

    def set_rows(self, rows: list[dict]):
        self._rows = list(rows)
        self._render()

    def _render(self):
        self.tree.delete(*self.tree.get_children())
        for i, row in enumerate(self._rows):
            values = [row.get(c[0], "") for c in self._columns]
            tags = []
            if row.get("_overdue"):
                tags.append("overdue")
            elif i % 2:
                tags.append("odd")
            self.tree.insert("", "end", values=values, tags=tags)

    def _sort_by(self, key):
        ascending = not self._sort_state.get(key, False)
        self._sort_state[key] = ascending

        def sort_key(row):
            v = row.get(key, "")
            return (0, v) if isinstance(v, (int, float)) else (1, str(v).lower())

        self._rows.sort(key=sort_key, reverse=not ascending)
        self._render()


class ScrollableFrame(ttk.Frame):
    """A vertically scrollable container. Add children to `.body`."""

    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self._canvas = tk.Canvas(self, bg=COLORS["card"], highlightthickness=0)
        vsb = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vsb.set)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.body = tk.Frame(self._canvas, bg=COLORS["card"])
        self._win = self._canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>",
                       lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>",
                          lambda e: self._canvas.itemconfigure(self._win, width=e.width))
        # Mouse-wheel scrolling while hovering the list.
        self.body.bind("<Enter>", lambda e: self._canvas.bind_all("<MouseWheel>", self._on_wheel))
        self.body.bind("<Leave>", lambda e: self._canvas.unbind_all("<MouseWheel>"))

    def _on_wheel(self, event):
        self._canvas.yview_scroll(int(-event.delta / 120), "units")

    def clear(self):
        for w in self.body.winfo_children():
            w.destroy()


class ChartCanvas(ttk.Frame):
    """A matplotlib Figure embedded in Tk, with a simple bar-chart helper."""

    def __init__(self, parent, figsize=(5, 3)):
        super().__init__(parent, style="Card.TFrame")
        self.figure = Figure(figsize=figsize, dpi=100, facecolor=COLORS["card"])
        self.ax = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    def bar(self, labels, values, title="", color=None, value_color=None):
        self.ax.clear()
        color = color or COLORS["accent"]
        if value_color:
            color = value_color
        bars = self.ax.bar(range(len(labels)), values, color=color)
        self.ax.set_xticks(range(len(labels)))
        self.ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
        self.ax.set_title(title, fontsize=11, color=COLORS["text"])
        self.ax.spines[["top", "right"]].set_visible(False)
        self.ax.tick_params(colors=COLORS["muted"], labelsize=8)
        for b, v in zip(bars, values):
            if v:
                self.ax.text(b.get_x() + b.get_width() / 2, v, str(v),
                             ha="center", va="bottom", fontsize=8, color=COLORS["text"])
        self.figure.tight_layout()
        self.canvas.draw()

    def grouped_bar(self, labels, series, title=""):
        """series: list of (name, values, color)."""
        self.ax.clear()
        n = len(series)
        width = 0.8 / max(n, 1)
        x = range(len(labels))
        for i, (name, values, color) in enumerate(series):
            offsets = [xi + i * width for xi in x]
            self.ax.bar(offsets, values, width=width, label=name, color=color)
        self.ax.set_xticks([xi + width * (n - 1) / 2 for xi in x])
        self.ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
        self.ax.set_title(title, fontsize=11, color=COLORS["text"])
        self.ax.spines[["top", "right"]].set_visible(False)
        self.ax.tick_params(colors=COLORS["muted"], labelsize=8)
        self.ax.legend(fontsize=8, frameon=False)
        self.figure.tight_layout()
        self.canvas.draw()
