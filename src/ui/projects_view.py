"""Tax Returns screen: open return projects split by type, with document
check-off. Each project groups the client's source documents (W-2, 1099, ...);
tick a document as it comes in, set Individual/Business, and mark the return
complete when you're done with it.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from config import RETURN_TYPES
from ui.theme import COLORS, FONTS
from ui.widgets import KPICard, ScrollableFrame

FILTERS = ["All", "Individual", "Business", "Unclassified"]


class ProjectsView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, style="TFrame", padding=20)
        self.app = app
        self.filter_type = "All"
        self.show_completed = tk.BooleanVar(value=False)
        self.selected_pkey: str | None = None
        self._row_to_pkey: dict[str, str] = {}

        ttk.Label(self, text="Tax returns", style="H1.TLabel").pack(anchor="w")
        ttk.Label(self,
                  text="Open returns and the client documents they're waiting on. "
                       "Tick documents as they arrive; mark a return complete when it's done.",
                  foreground=COLORS["muted"]).pack(anchor="w", pady=(2, 12))

        # KPI cards
        cards = ttk.Frame(self, style="TFrame")
        cards.pack(fill="x")
        self.kpi_open = KPICard(cards, "Open returns", COLORS["accent"])
        self.kpi_ind = KPICard(cards, "Individual", COLORS["text"])
        self.kpi_out = KPICard(cards, "Documents outstanding", COLORS["warn"])
        for i, c in enumerate((self.kpi_open, self.kpi_ind, self.kpi_out)):
            c.grid(row=0, column=i, padx=(0 if i == 0 else 10, 0), sticky="ew")
            cards.grid_columnconfigure(i, weight=1)

        # Filter chips
        filt = ttk.Frame(self, style="TFrame")
        filt.pack(fill="x", pady=(14, 8))
        self.filter_btns: dict[str, tk.Button] = {}
        for name in FILTERS:
            b = tk.Button(filt, text=name, relief="flat", bd=0, padx=12, pady=4,
                          font=FONTS["body"], cursor="hand2",
                          command=lambda n=name: self._set_filter(n))
            b.pack(side="left", padx=(0, 6))
            self.filter_btns[name] = b
        tk.Checkbutton(filt, text="Show completed", variable=self.show_completed,
                       bg=COLORS["bg"], font=FONTS["body"], command=self._refresh,
                       activebackground=COLORS["bg"]).pack(side="left", padx=(12, 0))

        # Master-detail body
        body = ttk.Frame(self, style="TFrame")
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=5)
        body.grid_columnconfigure(1, weight=6)
        body.grid_rowconfigure(0, weight=1)

        # Left: projects list
        left = ttk.Frame(body, style="TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.grid_rowconfigure(0, weight=1)
        left.grid_columnconfigure(0, weight=1)
        cols = ("client", "rtype", "docs", "days", "status")
        self.tree = ttk.Treeview(left, columns=cols, show="headings")
        for key, head, w, anc in [
            ("client", "Client / return", 200, "w"),
            ("rtype", "Type", 90, "w"),
            ("docs", "Docs in", 80, "center"),
            ("days", "Days open", 80, "center"),
            ("status", "Status", 90, "center"),
        ]:
            self.tree.heading(key, text=head)
            self.tree.column(key, width=w, anchor=anc)
        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.tree.tag_configure("overdue", foreground=COLORS["danger"])
        self.tree.tag_configure("done", foreground=COLORS["ok"])
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # Right: project detail
        self.detail = ttk.Frame(body, style="Card.TFrame", padding=14)
        self.detail.configure(borderwidth=1, relief="solid")
        self.detail.grid(row=0, column=1, sticky="nsew")
        self._build_detail_skeleton()

    # ---- detail skeleton -----------------------------------------------
    def _build_detail_skeleton(self):
        self.detail_client = tk.Label(self.detail, text="Select a return",
                                      font=FONTS["h1"], bg=COLORS["card"], fg=COLORS["text"])
        self.detail_client.pack(anchor="w")
        self.detail_meta = tk.Label(self.detail, text="", font=FONTS["body"],
                                    bg=COLORS["card"], fg=COLORS["muted"])
        self.detail_meta.pack(anchor="w", pady=(2, 10))

        controls = tk.Frame(self.detail, bg=COLORS["card"])
        controls.pack(fill="x", pady=(0, 10))
        tk.Label(controls, text="Return type:", bg=COLORS["card"], fg=COLORS["text"],
                 font=FONTS["body"]).pack(side="left")
        self.type_var = tk.StringVar()
        self.type_combo = ttk.Combobox(controls, textvariable=self.type_var, values=RETURN_TYPES,
                                       state="readonly", width=14)
        self.type_combo.pack(side="left", padx=(6, 14))
        self.type_combo.bind("<<ComboboxSelected>>", self._on_type_change)
        self.complete_btn = ttk.Button(controls, text="Mark complete", style="Accent.TButton",
                                       command=self._toggle_complete)
        self.complete_btn.pack(side="left")

        tk.Label(self.detail, text="Client documents", font=FONTS["h2"],
                 bg=COLORS["card"], fg=COLORS["text"]).pack(anchor="w")
        tk.Label(self.detail, text="Tick each document as it comes in from the client.",
                 font=FONTS["kpi_label"], bg=COLORS["card"], fg=COLORS["muted"]).pack(anchor="w")
        self.doc_list = ScrollableFrame(self.detail)
        self.doc_list.pack(fill="both", expand=True, pady=(8, 0))

    # ---- filters --------------------------------------------------------
    def _set_filter(self, name):
        self.filter_type = name
        self._refresh()

    def _projects_for_display(self):
        projects = (self.app.data or {}).get("projects", [])
        out = []
        for p in projects:
            if not self.show_completed.get() and p["completed"]:
                continue
            if self.filter_type != "All" and p["return_type"] != self.filter_type:
                continue
            out.append(p)
        return out

    # ---- data refresh ---------------------------------------------------
    def on_data(self):
        self._refresh()

    def _refresh(self):
        data = self.app.data or {}
        totals = data.get("project_totals", {})
        self.kpi_open.set_value(totals.get("open_total", 0))
        self.kpi_ind.set_value(totals.get("open_individual", 0))
        self.kpi_out.set_value(totals.get("docs_outstanding", 0))

        # Filter chip styling + counts
        all_projects = data.get("projects", [])
        relevant = [p for p in all_projects if self.show_completed.get() or not p["completed"]]
        counts = {"All": len(relevant)}
        for t in ("Individual", "Business", "Unclassified"):
            counts[t] = sum(1 for p in relevant if p["return_type"] == t)
        for name, btn in self.filter_btns.items():
            active = name == self.filter_type
            btn.config(text=f"{name} ({counts.get(name, 0)})",
                       bg=COLORS["accent"] if active else "#e3e8ee",
                       fg="white" if active else COLORS["text"])

        # Rebuild project list
        self.tree.delete(*self.tree.get_children())
        self._row_to_pkey.clear()
        display = self._projects_for_display()
        for p in display:
            tags = []
            if p["completed"]:
                tags.append("done")
            elif p["overdue"]:
                tags.append("overdue")
            status = "Done" if p["completed"] else ("Overdue" if p["overdue"] else "Open")
            iid = self.tree.insert("", "end", values=(
                p["client"], p["return_type"], f"{p['received_docs']}/{p['total_docs']}",
                p["days_open"], status), tags=tags)
            self._row_to_pkey[iid] = p["project_key"]

        # Keep / choose a selection
        keys = [p["project_key"] for p in display]
        if self.selected_pkey not in keys:
            self.selected_pkey = keys[0] if keys else None
        if self.selected_pkey:
            for iid, pk in self._row_to_pkey.items():
                if pk == self.selected_pkey:
                    self.tree.selection_set(iid)
                    break
        self._render_detail()

    def _on_select(self, _event):
        sel = self.tree.selection()
        if sel:
            self.selected_pkey = self._row_to_pkey.get(sel[0])
            self._render_detail()

    def _current_project(self):
        for p in (self.app.data or {}).get("projects", []):
            if p["project_key"] == self.selected_pkey:
                return p
        return None

    # ---- detail rendering ----------------------------------------------
    def _render_detail(self):
        p = self._current_project()
        self.doc_list.clear()
        if not p:
            self.detail_client.config(text="Select a return")
            self.detail_meta.config(text="")
            self.type_var.set("")
            self.complete_btn.config(text="Mark complete", state="disabled")
            return

        self.detail_client.config(text=p["client"])
        self.detail_meta.config(
            text=f"{p['received_docs']} of {p['total_docs']} documents in "
                 f"({p['pct_complete']}%)  •  open {p['days_open']} days"
                 + ("  •  OVERDUE" if p["overdue"] and not p["completed"] else ""))
        self.type_var.set(p["return_type"])
        self.complete_btn.config(state="normal",
                                 text="Reopen" if p["completed"] else "Mark complete")

        for doc in p["documents"]:
            var = tk.BooleanVar(value=doc["received"])
            fg = COLORS["ok"] if doc["received"] else (
                COLORS["danger"] if doc["overdue"] else COLORS["text"])
            label = f"{doc['title']}   —   {doc['status']}  ({doc['age_days']}d)"
            cb = tk.Checkbutton(
                self.doc_list.body, text=label, variable=var, anchor="w",
                bg=COLORS["card"], fg=fg, font=FONTS["body"], activebackground=COLORS["card"],
                selectcolor="white",
                command=lambda k=doc["item_key"], v=var: self._toggle_doc(k, v))
            cb.pack(fill="x", anchor="w", pady=1)

    # ---- actions --------------------------------------------------------
    def _toggle_doc(self, item_key, var):
        self.app.store.set_received(item_key, bool(var.get()), self.app.today())
        self.app.recompute()  # refreshes counts/KPIs everywhere

    def _on_type_change(self, _event):
        if self.selected_pkey:
            self.app.store.set_project_type(self.selected_pkey, self.type_var.get())
            self.app.recompute()

    def _toggle_complete(self):
        p = self._current_project()
        if not p:
            return
        self.app.store.set_project_completed(self.selected_pkey, not p["completed"], self.app.today())
        self.app.recompute()
