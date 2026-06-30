"""Per-person screen: pick a staff member and see their pending queue."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ui.theme import COLORS, FONTS
from ui.widgets import KPICard, SortableTable


class PersonView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, style="TFrame", padding=20)
        self.app = app

        ttk.Label(self, text="By staff member", style="H1.TLabel").pack(anchor="w")
        ttk.Label(self, text="See one person's pending documents. Overdue items are highlighted.",
                  foreground=COLORS["muted"]).pack(anchor="w", pady=(2, 12))

        picker = ttk.Frame(self, style="TFrame")
        picker.pack(fill="x")
        ttk.Label(picker, text="Staff member:").pack(side="left", padx=(0, 8))
        self.person_var = tk.StringVar()
        self.combo = ttk.Combobox(picker, textvariable=self.person_var, state="readonly", width=32)
        self.combo.pack(side="left")
        self.combo.bind("<<ComboboxSelected>>", lambda e: self._render_person())

        cards = ttk.Frame(self, style="TFrame")
        cards.pack(fill="x", pady=(14, 8))
        self.kpi_pending = KPICard(cards, "Pending", COLORS["accent"])
        self.kpi_overdue = KPICard(cards, "Overdue", COLORS["danger"])
        self.kpi_max = KPICard(cards, "Oldest (days)", COLORS["text"])
        for i, c in enumerate((self.kpi_pending, self.kpi_overdue, self.kpi_max)):
            c.grid(row=0, column=i, padx=(0 if i == 0 else 10, 0), sticky="ew")
            cards.grid_columnconfigure(i, weight=1)

        self.table = SortableTable(self, [
            ("client", "Client", 200, "w"),
            ("title", "Document / work", 360, "w"),
            ("status", "Status", 140, "w"),
            ("age_days", "Days open", 100, "center"),
        ], height=12)
        self.table.pack(fill="both", expand=True, pady=(8, 0))

    def on_data(self):
        data = self.app.data
        if not data:
            return
        names = sorted({it["assignee"] for it in data["items"]})
        self.combo["values"] = names
        if names and self.person_var.get() not in names:
            self.person_var.set(names[0])
        self._render_person()

    def _render_person(self):
        data = self.app.data
        if not data:
            return
        name = self.person_var.get()
        items = [it for it in data["items"] if it["assignee"] == name]
        items.sort(key=lambda x: x["age_days"], reverse=True)

        pending = len(items)
        overdue = sum(1 for it in items if it["overdue"])
        ages = [it["age_days"] for it in items]
        self.kpi_pending.set_value(pending)
        self.kpi_overdue.set_value(overdue)
        self.kpi_max.set_value(max(ages) if ages else 0)

        rows = [
            {
                "client": it["client"],
                "title": it["title"],
                "status": it["status"],
                "age_days": it["age_days"],
                "_overdue": it["overdue"],
            }
            for it in items
        ]
        self.table.set_rows(rows)
