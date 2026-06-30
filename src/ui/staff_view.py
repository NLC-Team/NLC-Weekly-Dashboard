"""Staff management: add staff members and assign their access roles."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ui.theme import COLORS, FONTS

ROLES = ["Admin", "Manager", "Viewer"]

ROLE_DESCRIPTIONS = {
    "Admin": "Full access — import data, edit settings, manage staff",
    "Manager": "View analytics and mark items received / complete",
    "Viewer": "Read-only access to the dashboard",
}

ROLE_BADGE_COLORS = {
    "Admin":   ("#fee2e2", "#991b1b"),
    "Manager": ("#dbeafe", "#1e40af"),
    "Viewer":  ("#d1fae5", "#065f46"),
}


class StaffView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, style="TFrame", padding=24)
        self.app = app
        self._build()

    # ------------------------------------------------------------------ build
    def _build(self):
        ttk.Label(self, text="Staff & Roles", style="H1.TLabel").pack(anchor="w", pady=(0, 4))
        ttk.Label(
            self,
            text="Add team members and record what level of access they hold in the dashboard.",
            style="Muted.TLabel",
            background=COLORS["bg"],
            foreground=COLORS["muted"],
        ).pack(anchor="w", pady=(0, 20))

        # Role legend card
        legend_card = self._card("Access levels")
        for role in ROLES:
            bg, fg = ROLE_BADGE_COLORS[role]
            row = ttk.Frame(legend_card, style="Card.TFrame")
            row.pack(fill="x", pady=3)
            badge = tk.Label(row, text=f"  {role}  ", bg=bg, fg=fg,
                             font=("Segoe UI Semibold", 10), relief="flat", padx=4, pady=2)
            badge.pack(side="left", padx=(0, 12))
            tk.Label(row, text=ROLE_DESCRIPTIONS[role], bg=COLORS["card"],
                     fg=COLORS["text"], font=FONTS["body"]).pack(side="left")

        # Add staff card
        add_card = self._card("Add staff member")
        grid = ttk.Frame(add_card, style="Card.TFrame")
        grid.pack(anchor="w")

        tk.Label(grid, text="Full name", bg=COLORS["card"], fg=COLORS["text"],
                 font=("Segoe UI Semibold", 11)).grid(row=0, column=0, sticky="w", padx=(0, 12), pady=(0, 4))
        tk.Label(grid, text="Role", bg=COLORS["card"], fg=COLORS["text"],
                 font=("Segoe UI Semibold", 11)).grid(row=0, column=1, sticky="w", padx=(0, 12), pady=(0, 4))

        self.name_var = tk.StringVar()
        self.role_var = tk.StringVar(value="Viewer")

        name_entry = ttk.Entry(grid, textvariable=self.name_var, width=30, font=FONTS["body"])
        name_entry.grid(row=1, column=0, padx=(0, 12), ipady=4)
        name_entry.bind("<Return>", lambda _e: self._add_staff())

        role_combo = ttk.Combobox(grid, textvariable=self.role_var, values=ROLES,
                                  width=14, state="readonly", font=FONTS["body"])
        role_combo.grid(row=1, column=1, padx=(0, 12), ipady=4)

        ttk.Button(grid, text="Add member", style="Accent.TButton",
                   command=self._add_staff).grid(row=1, column=2)

        self.add_msg = tk.Label(add_card, text="", bg=COLORS["card"],
                                fg=COLORS["ok"], font=FONTS["small"])
        self.add_msg.pack(anchor="w", pady=(8, 0))

        # Staff list card
        list_card = self._card("Current staff")
        self._build_tree(list_card)

    def _build_tree(self, parent):
        cols = ("name", "role", "description")
        tree_frame = ttk.Frame(parent, style="Card.TFrame")
        tree_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=10,
                                 selectmode="browse")
        self.tree.heading("name", text="Name")
        self.tree.heading("role", text="Role")
        self.tree.heading("description", text="Access level")
        self.tree.column("name", width=200, minwidth=140)
        self.tree.column("role", width=110, minwidth=80)
        self.tree.column("description", width=400, minwidth=220)

        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        action_row = ttk.Frame(parent, style="Card.TFrame")
        action_row.pack(anchor="w", pady=(10, 0))

        ttk.Button(action_row, text="Remove selected",
                   command=self._remove_staff).pack(side="left")

        tk.Label(action_row, text="Role:", bg=COLORS["card"], fg=COLORS["text"],
                 font=FONTS["body"]).pack(side="left", padx=(16, 6))
        self.edit_role_var = tk.StringVar()
        self.edit_role_combo = ttk.Combobox(action_row, textvariable=self.edit_role_var,
                                            values=ROLES, width=12, state="disabled",
                                            font=FONTS["body"])
        self.edit_role_combo.pack(side="left", padx=(0, 8))
        self.edit_role_combo.bind("<<ComboboxSelected>>", self._on_role_selected)

        self.tree_msg = tk.Label(parent, text="", bg=COLORS["card"],
                                 fg=COLORS["danger"], font=FONTS["small"])
        self.tree_msg.pack(anchor="w", pady=(6, 0))

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        self._refresh_tree()

    # --------------------------------------------------------- tree helpers
    def _refresh_tree(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        for member in self.app.store.get_staff():
            self.tree.insert(
                "", "end", iid=member["name"],
                values=(member["name"], member["role"],
                        ROLE_DESCRIPTIONS.get(member["role"], "")),
            )

    def _add_staff(self):
        name = self.name_var.get().strip()
        role = self.role_var.get()
        if not name:
            self.add_msg.config(text="Please enter a name.", fg=COLORS["danger"])
            return
        self.app.store.upsert_staff(name, role, self.app.today())
        self.name_var.set("")
        self.add_msg.config(text=f"{name} added as {role}.", fg=COLORS["ok"])
        self._refresh_tree()

    def _remove_staff(self):
        sel = self.tree.selection()
        if not sel:
            self.tree_msg.config(text="Select a staff member first.")
            return
        name = sel[0]
        if messagebox.askyesno("Remove staff member", f"Remove {name} from the directory?",
                               icon="warning"):
            self.app.store.remove_staff(name)
            self.tree_msg.config(text="")
            self._refresh_tree()

    def _on_tree_select(self, _event=None):
        sel = self.tree.selection()
        if sel:
            current_role = self.tree.set(sel[0], "role")
            self.edit_role_var.set(current_role)
            self.edit_role_combo.configure(state="readonly")
            self.tree_msg.config(text="")
        else:
            self.edit_role_combo.configure(state="disabled")
            self.edit_role_var.set("")

    def _on_role_selected(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        name = sel[0]
        new_role = self.edit_role_var.get()
        self.app.store.upsert_staff(name, new_role, self.app.today())
        self.tree_msg.config(text=f"{name} updated to {new_role}.", fg=COLORS["ok"])
        self._refresh_tree()
        self.tree.selection_set(name)

    def _card(self, title: str) -> ttk.Frame:
        card = ttk.Frame(self, style="Card.TFrame", padding=18)
        card.configure(borderwidth=1, relief="solid")
        card.pack(fill="x", pady=(0, 14))
        ttk.Label(card, text=title, style="H2.TLabel").pack(anchor="w", pady=(0, 10))
        return card

    def on_data(self):
        pass
