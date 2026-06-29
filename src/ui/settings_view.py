"""Settings screen: overdue threshold and database location."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ui.theme import COLORS, FONTS


class SettingsView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, style="TFrame", padding=20)
        self.app = app

        ttk.Label(self, text="Settings", style="H1.TLabel").pack(anchor="w", pady=(0, 14))

        # --- Overdue threshold ---
        card1 = self._card("Overdue threshold")
        ttk.Label(card1, text="Flag a document as overdue once it has been open more than this many days.",
                  background=COLORS["card"], foreground=COLORS["muted"]).pack(anchor="w", pady=(0, 8))
        row = ttk.Frame(card1, style="Card.TFrame")
        row.pack(anchor="w")
        self.days_var = tk.IntVar(value=self.app.overdue_days)
        self.spin = ttk.Spinbox(row, from_=1, to=365, width=6, textvariable=self.days_var)
        self.spin.pack(side="left")
        tk.Label(row, text="days", bg=COLORS["card"], fg=COLORS["text"], font=FONTS["body"]).pack(
            side="left", padx=(6, 16))
        ttk.Button(row, text="Apply", style="Accent.TButton", command=self._apply_days).pack(side="left")

        # --- Database location ---
        card2 = self._card("Data storage location")
        ttk.Label(card2,
                  text="Where the dashboard keeps its history and settings. Point this at a shared "
                       "network folder so the whole team's imports build up in one place.",
                  background=COLORS["card"], foreground=COLORS["muted"], wraplength=620,
                  justify="left").pack(anchor="w", pady=(0, 8))
        self.db_lbl = tk.Label(card2, text="", bg=COLORS["card"], fg=COLORS["text"],
                               font=("Consolas", 9), anchor="w", justify="left", wraplength=620)
        self.db_lbl.pack(anchor="w", pady=(0, 8))
        ttk.Button(card2, text="Change location...", command=self._change_db).pack(anchor="w")

        # --- Column mapping note ---
        card3 = self._card("Column mapping & statuses")
        ttk.Label(card3,
                  text="Mappings and pending-status choices are remembered per CSV layout and applied "
                       "automatically. To change them, go to Import and re-select your file.",
                  background=COLORS["card"], foreground=COLORS["muted"], wraplength=620,
                  justify="left").pack(anchor="w")

        self.info_lbl = ttk.Label(self, text="", foreground=COLORS["muted"])
        self.info_lbl.pack(anchor="w", pady=(12, 0))

    def _card(self, title):
        card = ttk.Frame(self, style="Card.TFrame", padding=16)
        card.configure(borderwidth=1, relief="solid")
        card.pack(fill="x", pady=(0, 12))
        ttk.Label(card, text=title, style="H2.TLabel").pack(anchor="w", pady=(0, 6))
        return card

    def _apply_days(self):
        try:
            days = int(self.days_var.get())
        except (tk.TclError, ValueError):
            messagebox.showwarning("Invalid value", "Please enter a whole number of days.")
            return
        if days < 1:
            days = 1
        self.app.set_overdue_days(days)
        self.app.recompute()
        self.info_lbl.config(text=f"Overdue threshold set to {days} days.")

    def _change_db(self):
        path = filedialog.asksaveasfilename(
            title="Choose database file (existing or new)",
            defaultextension=".db",
            filetypes=[("Dashboard database", "*.db"), ("All files", "*.*")],
            initialfile="dashboard.db",
        )
        if not path:
            return
        try:
            self.app.change_db(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Could not open database", str(exc))
            return
        self.on_data()
        messagebox.showinfo("Data location changed", f"Now using:\n{path}")

    def on_data(self):
        self.db_lbl.config(text=str(self.app.store.db_path))
        self.days_var.set(self.app.overdue_days)
