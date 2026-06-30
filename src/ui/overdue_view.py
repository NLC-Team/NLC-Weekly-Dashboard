"""Overdue alerts screen: the 'these are taking too long' list, per person."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ui.theme import COLORS, FONTS
from ui.widgets import SortableTable


class OverdueView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, style="TFrame", padding=20)
        self.app = app

        ttk.Label(self, text="Overdue statements", style="H1.TLabel").pack(anchor="w")
        self.subtitle = ttk.Label(self, text="", foreground=COLORS["muted"])
        self.subtitle.pack(anchor="w", pady=(2, 12))

        # Per-person alert banner (color set dynamically in on_data).
        self.banner = tk.Frame(self, highlightthickness=1)
        self.banner.pack(fill="x", pady=(0, 12))
        self.banner_body = tk.Frame(self.banner)
        self.banner_body.pack(fill="x", padx=12, pady=10)

        self.table = SortableTable(self, [
            ("assignee", "Staff member", 180, "w"),
            ("client", "Client", 180, "w"),
            ("title", "Document / work", 320, "w"),
            ("status", "Status", 130, "w"),
            ("age_days", "Days open", 100, "center"),
            ("over_by", "Days overdue", 110, "center"),
        ], height=14)
        self.table.pack(fill="both", expand=True)

    def on_data(self):
        data = self.app.data
        if not data:
            return
        overdue = data["overdue"]
        threshold = self.app.overdue_days
        self.subtitle.config(
            text=f"{len(overdue)} item(s) open more than {threshold} days, oldest first. "
                 f"Click 'Staff member' to group by person.")

        # Banner: one line per person, worst-offender first.
        for w in self.banner_body.winfo_children():
            w.destroy()
        by_person: dict[str, list] = {}
        for it in overdue:
            by_person.setdefault(it["assignee"], []).append(it)

        if not by_person:
            banner_bg = "#f0fdf4"
            banner_border = "#86efac"
            self.banner.configure(bg=banner_bg, highlightbackground=banner_border)
            self.banner_body.configure(bg=banner_bg)
            tk.Label(self.banner_body, text="No overdue items. Nice work!",
                     bg=banner_bg, fg=COLORS["ok"], font=FONTS["h2"]).pack(anchor="w")
        else:
            banner_bg = COLORS["danger_bg"]
            self.banner.configure(bg=banner_bg, highlightbackground=COLORS["danger"])
            self.banner_body.configure(bg=banner_bg)
            tk.Label(self.banner_body, text="Action needed — longest-waiting items per person:",
                     bg=banner_bg, fg=COLORS["danger"],
                     font=("Segoe UI Semibold", 11)).pack(anchor="w", pady=(0, 4))
            ranked = sorted(by_person.items(), key=lambda kv: len(kv[1]), reverse=True)
            for person, items in ranked:
                worst = max(items, key=lambda x: x["age_days"])
                msg = (f"• {person}: {len(items)} overdue — oldest is "
                       f"\"{worst['title']}\" ({worst['client']}) at {worst['age_days']} days")
                tk.Label(self.banner_body, text=msg, bg=banner_bg, fg=COLORS["text"],
                         font=FONTS["body"], anchor="w", justify="left").pack(anchor="w")

        rows = [
            {
                "assignee": it["assignee"],
                "client": it["client"],
                "title": it["title"],
                "status": it["status"],
                "age_days": it["age_days"],
                "over_by": it["age_days"] - threshold,
                "_overdue": True,
            }
            for it in overdue
        ]
        self.table.set_rows(rows)
