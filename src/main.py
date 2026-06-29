"""Karbon Pending Dashboard -- application entry point.

Builds the main window (sidebar navigation + swappable content views), owns the
shared application state (the open database, current settings, and the computed
dashboard data), and coordinates refreshes when data or settings change.
"""
from __future__ import annotations

import os
import sys
from datetime import date

# Allow running as `python src/main.py` (script dir on path) and when frozen.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

import config  # noqa: E402
from data import service  # noqa: E402
from data.store import Store  # noqa: E402
from ui import theme  # noqa: E402
from ui.import_view import ImportView  # noqa: E402
from ui.overdue_view import OverdueView  # noqa: E402
from ui.overview_view import OverviewView  # noqa: E402
from ui.person_view import PersonView  # noqa: E402
from ui.projects_view import ProjectsView  # noqa: E402
from ui.settings_view import SettingsView  # noqa: E402

NAV = [
    ("import", "Import"),
    ("overview", "Overview"),
    ("projects", "Tax returns"),
    ("person", "By person"),
    ("overdue", "Overdue"),
    ("settings", "Settings"),
]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(config.APP_NAME)
        self.geometry("1180x760")
        self.minsize(960, 640)
        theme.apply_theme(self)

        self.store = Store(config.active_db_path())
        self.overdue_days = int(self.store.get_setting("overdue_days", config.DEFAULT_OVERDUE_DAYS))
        self.pending_statuses = list(self.store.get_setting("pending_statuses", []))
        self.data: dict | None = None

        self._build_layout()
        self.recompute()
        # Land on Overview if we already have history, otherwise the Import screen.
        self.navigate("overview" if self.store.last_import() else "import")

    # ---- shared state helpers ------------------------------------------
    def today(self) -> date:
        return date.today()

    def set_overdue_days(self, days: int):
        self.overdue_days = days
        self.store.set_setting("overdue_days", days)

    def set_pending_statuses(self, statuses: list[str]):
        self.pending_statuses = list(statuses)
        self.store.set_setting("pending_statuses", self.pending_statuses)

    def change_db(self, path: str):
        new_store = Store(path)  # raises if it cannot be opened
        self.store.close()
        self.store = new_store
        cfg = config.read_app_config()
        cfg["db_path"] = str(path)
        config.write_app_config(cfg)
        self.overdue_days = int(self.store.get_setting("overdue_days", config.DEFAULT_OVERDUE_DAYS))
        self.pending_statuses = list(self.store.get_setting("pending_statuses", []))
        self.recompute()

    def recompute(self):
        self.data = service.dashboard_data(self.store, self.today(), self.overdue_days)
        for view in self.views.values():
            view.on_data()

    # ---- layout ---------------------------------------------------------
    def _build_layout(self):
        sidebar = tk.Frame(self, bg=theme.COLORS["sidebar"], width=200)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        tk.Label(sidebar, text="Karbon\nPending Dashboard", bg=theme.COLORS["sidebar"],
                 fg="white", font=("Segoe UI Semibold", 13), justify="left").pack(
            anchor="w", padx=16, pady=(22, 24))

        self.nav_buttons: dict[str, tk.Button] = {}
        for key, label in NAV:
            btn = tk.Button(sidebar, text=label, anchor="w", relief="flat",
                            bg=theme.COLORS["sidebar"], fg=theme.COLORS["sidebar_text"],
                            activebackground=theme.COLORS["sidebar_active"], activeforeground="white",
                            font=theme.FONTS["nav"], bd=0, padx=18, pady=10,
                            command=lambda k=key: self.navigate(k))
            btn.pack(fill="x")
            self.nav_buttons[key] = btn

        self.content = ttk.Frame(self, style="TFrame")
        self.content.pack(side="left", fill="both", expand=True)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self.views = {
            "import": ImportView(self.content, self),
            "overview": OverviewView(self.content, self),
            "projects": ProjectsView(self.content, self),
            "person": PersonView(self.content, self),
            "overdue": OverdueView(self.content, self),
            "settings": SettingsView(self.content, self),
        }
        for view in self.views.values():
            view.grid(row=0, column=0, sticky="nsew")

    def navigate(self, key: str):
        self.views[key].tkraise()
        for k, btn in self.nav_buttons.items():
            active = k == key
            btn.configure(bg=theme.COLORS["sidebar_active"] if active else theme.COLORS["sidebar"],
                          fg="white" if active else theme.COLORS["sidebar_text"])


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
