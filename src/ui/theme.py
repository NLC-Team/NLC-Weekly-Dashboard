"""Shared colours, fonts, and ttk styling for a clean, consistent look."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# A calm, professional palette suitable for an accounting firm.
COLORS = {
    "bg": "#f4f6f8",
    "sidebar": "#1f2d3d",
    "sidebar_active": "#2c4055",
    "sidebar_text": "#cdd7e2",
    "card": "#ffffff",
    "accent": "#2563eb",
    "text": "#1f2d3d",
    "muted": "#6b7280",
    "ok": "#16a34a",
    "warn": "#d97706",
    "danger": "#dc2626",
    "danger_bg": "#fde8e8",
    "border": "#e3e8ee",
}

FONTS = {
    "h1": ("Segoe UI Semibold", 18),
    "h2": ("Segoe UI Semibold", 13),
    "body": ("Segoe UI", 10),
    "kpi": ("Segoe UI", 26, "bold"),
    "kpi_label": ("Segoe UI", 9),
    "nav": ("Segoe UI", 11),
}


def apply_theme(root: tk.Tk) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(bg=COLORS["bg"])
    style.configure("TFrame", background=COLORS["bg"])
    style.configure("Card.TFrame", background=COLORS["card"], relief="flat")
    style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=FONTS["body"])
    style.configure("Card.TLabel", background=COLORS["card"], foreground=COLORS["text"])
    style.configure("H1.TLabel", font=FONTS["h1"], background=COLORS["bg"], foreground=COLORS["text"])
    style.configure("H2.TLabel", font=FONTS["h2"], background=COLORS["card"], foreground=COLORS["text"])
    style.configure("Muted.TLabel", foreground=COLORS["muted"], background=COLORS["card"], font=FONTS["kpi_label"])
    style.configure("KPI.TLabel", font=FONTS["kpi"], background=COLORS["card"], foreground=COLORS["accent"])

    style.configure("TButton", font=FONTS["body"], padding=6)
    style.configure("Accent.TButton", font=("Segoe UI Semibold", 10), padding=8,
                    background=COLORS["accent"], foreground="white", borderwidth=0)
    style.map("Accent.TButton",
              background=[("active", "#1d4ed8"), ("disabled", "#9db8f0")])

    # Treeview (tables)
    style.configure("Treeview", font=FONTS["body"], rowheight=26,
                    background="white", fieldbackground="white", foreground=COLORS["text"])
    style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10), padding=4)
    style.map("Treeview", background=[("selected", "#dbe7ff")], foreground=[("selected", COLORS["text"])])

    style.configure("TCombobox", padding=4)
    style.configure("Horizontal.TScale", background=COLORS["bg"])
