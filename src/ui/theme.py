"""Shared colours, fonts, and ttk styling for a clean, consistent look."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# A calm, professional palette suitable for an accounting firm.
COLORS = {
    "bg": "#f0f4f8",
    "sidebar": "#141e2d",
    "sidebar_active": "#1e3a5f",
    "sidebar_hover": "#1a2e47",
    "sidebar_text": "#e2eaf5",
    "card": "#ffffff",
    "accent": "#1d4ed8",
    "accent_light": "#dbeafe",
    "text": "#0f1923",
    "muted": "#374151",
    "ok": "#15803d",
    "warn": "#b45309",
    "danger": "#b91c1c",
    "danger_bg": "#fee2e2",
    "border": "#d1d9e0",
    "card_border": "#c8d3dc",
}

FONTS = {
    "h1": ("Segoe UI Semibold", 20),
    "h2": ("Segoe UI Semibold", 15),
    "body": ("Segoe UI", 11),
    "small": ("Segoe UI", 10),
    "kpi": ("Segoe UI", 28, "bold"),
    "kpi_label": ("Segoe UI Semibold", 10),
    "nav": ("Segoe UI Semibold", 12),
    "nav_header": ("Segoe UI Semibold", 14),
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
    style.configure("Card.TLabel", background=COLORS["card"], foreground=COLORS["text"], font=FONTS["body"])
    style.configure("H1.TLabel", font=FONTS["h1"], background=COLORS["bg"], foreground=COLORS["text"])
    style.configure("H2.TLabel", font=FONTS["h2"], background=COLORS["card"], foreground=COLORS["text"])
    style.configure("Muted.TLabel", foreground=COLORS["muted"], background=COLORS["card"], font=FONTS["small"])
    style.configure("KPI.TLabel", font=FONTS["kpi"], background=COLORS["card"], foreground=COLORS["accent"])

    style.configure("TButton", font=FONTS["body"], padding=7)
    style.configure("Accent.TButton", font=("Segoe UI Semibold", 11), padding=(10, 7),
                    background=COLORS["accent"], foreground="white", borderwidth=0)
    style.map("Accent.TButton",
              background=[("active", "#1e40af"), ("disabled", "#93aee8")])

    # Treeview (tables)
    style.configure("Treeview", font=FONTS["body"], rowheight=30,
                    background="white", fieldbackground="white", foreground=COLORS["text"])
    style.configure("Treeview.Heading", font=("Segoe UI Semibold", 11), padding=6,
                    background=COLORS["bg"], foreground=COLORS["text"])
    style.map("Treeview", background=[("selected", COLORS["accent_light"])],
              foreground=[("selected", COLORS["text"])])

    style.configure("TCombobox", padding=5, font=FONTS["body"])
    style.configure("TEntry", padding=5, font=FONTS["body"])
    style.configure("TSpinbox", padding=5, font=FONTS["body"])
    style.configure("Horizontal.TScale", background=COLORS["bg"])
