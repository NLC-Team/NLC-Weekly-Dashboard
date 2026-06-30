"""Overview screen: KPI cards, a workload chart, and the team leaderboard."""
from __future__ import annotations

from tkinter import ttk

from ui.theme import COLORS
from ui.widgets import ChartCanvas, KPICard, SortableTable


class OverviewView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, style="TFrame", padding=20)
        self.app = app

        ttk.Label(self, text="Team overview", style="H1.TLabel").pack(anchor="w")
        self.subtitle = ttk.Label(self, text="", foreground=COLORS["muted"])
        self.subtitle.pack(anchor="w", pady=(2, 14))

        # KPI cards row
        cards = ttk.Frame(self, style="TFrame")
        cards.pack(fill="x")
        self.kpi_pending = KPICard(cards, "Pending documents", COLORS["accent"])
        self.kpi_overdue = KPICard(cards, "Overdue", COLORS["danger"])
        self.kpi_staff = KPICard(cards, "Staff with work", COLORS["text"])
        self.kpi_avg = KPICard(cards, "Avg. days open", COLORS["warn"])
        for i, card in enumerate((self.kpi_pending, self.kpi_overdue, self.kpi_staff, self.kpi_avg)):
            card.grid(row=0, column=i, padx=(0 if i == 0 else 10, 0), sticky="ew")
            cards.grid_columnconfigure(i, weight=1)

        # Charts row
        charts = ttk.Frame(self, style="TFrame")
        charts.pack(fill="x", pady=(16, 8))
        self.workload_chart = ChartCanvas(charts, figsize=(5.2, 3))
        self.workload_chart.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.age_chart = ChartCanvas(charts, figsize=(5.2, 3))
        self.age_chart.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        charts.grid_columnconfigure(0, weight=1)
        charts.grid_columnconfigure(1, weight=1)

        # Leaderboard table
        ttk.Label(self, text="Pending workload by staff member", style="H1.TLabel").pack(
            anchor="w", pady=(10, 6))
        self.table = SortableTable(self, [
            ("assignee", "Staff member", 200, "w"),
            ("pending_count", "Pending", 90, "center"),
            ("overdue_count", "Overdue", 90, "center"),
            ("avg_age", "Avg days", 90, "center"),
            ("max_age", "Oldest (days)", 110, "center"),
            ("oldest_title", "Oldest item", 320, "w"),
        ], height=10)
        self.table.pack(fill="both", expand=True)

    def on_data(self):
        data = self.app.data
        if not data:
            return
        t = data["totals"]
        self.kpi_pending.set_value(t["total_pending"])
        self.kpi_overdue.set_value(t["total_overdue"])
        self.kpi_staff.set_value(t["staff_count"])
        self.kpi_avg.set_value(t["avg_age"])

        li = self.app.store.last_import()
        if li:
            self.subtitle.config(
                text=f"Last import: {li['file_name']} on {li['imported_at']}  •  "
                     f"overdue = more than {self.app.overdue_days} days open")

        rows = data["per_assignee"]
        # Workload chart: pending vs overdue per staff (top 10)
        top = rows[:10]
        labels = [r["assignee"].split()[0] for r in top]
        self.workload_chart.grouped_bar(
            labels,
            [
                ("Pending", [r["pending_count"] for r in top], COLORS["accent"]),
                ("Overdue", [r["overdue_count"] for r in top], COLORS["danger"]),
            ],
            title="Workload by staff member",
        )
        # Age distribution chart
        dist = data["age_distribution"]
        self.age_chart.bar([d[0] for d in dist], [d[1] for d in dist],
                           title="How long items have been open", color=COLORS["warn"])

        self.table.set_rows(rows)
