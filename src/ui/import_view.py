"""Import screen: pick a CSV, map columns, choose pending statuses, import."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from config import LOGICAL_FIELDS, REQUIRED_FIELDS
from data import importer, service
from ui.theme import COLORS, FONTS

# Words that usually mean "already done" -> not pending by default.
_DONE_HINTS = ("sent", "complete", "done", "closed", "filed", "delivered", "archived")


class ImportView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, style="TFrame", padding=20)
        self.app = app
        self.df = None
        self.columns: list[str] = []
        self.signature = ""
        self.map_vars: dict[str, tk.StringVar] = {}
        self.status_vars: dict[str, tk.BooleanVar] = {}

        ttk.Label(self, text="Import a Karbon export", style="H1.TLabel").pack(anchor="w")
        ttk.Label(self,
                  text="Choose your CSV export, confirm which columns mean what, and pick which "
                       "statuses count as 'pending to send'.",
                  style="TLabel", foreground=COLORS["muted"]).pack(anchor="w", pady=(2, 14))

        ttk.Button(self, text="Choose CSV file...", style="Accent.TButton",
                   command=self._choose_file).pack(anchor="w")
        self.file_lbl = ttk.Label(self, text="No file selected.", foreground=COLORS["muted"])
        self.file_lbl.pack(anchor="w", pady=(6, 12))

        # Body holds the mapping + status panels once a file is chosen.
        self.body = ttk.Frame(self, style="TFrame")
        self.body.pack(fill="both", expand=True)

    # ---- file selection -------------------------------------------------
    def _choose_file(self):
        path = filedialog.askopenfilename(
            title="Select Karbon CSV export",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self.df = importer.load_csv(path)
        except Exception as exc:  # noqa: BLE001 - surface any parse error to the user
            messagebox.showerror("Could not read file", f"There was a problem reading that CSV:\n\n{exc}")
            return
        self.path = path
        self.columns = list(self.df.columns)
        self.signature = importer.header_signature(self.columns)
        self.file_lbl.config(text=f"Loaded: {path}  ({len(self.df)} rows, {len(self.columns)} columns)")
        self._build_mapping()

    # ---- mapping + status panels ---------------------------------------
    def _build_mapping(self):
        for w in self.body.winfo_children():
            w.destroy()

        saved = self.app.store.get_mapping(self.signature)
        mapping = saved or importer.guess_mapping(self.columns)

        # --- column mapping panel ---
        map_card = ttk.Frame(self.body, style="Card.TFrame", padding=16)
        map_card.configure(borderwidth=1, relief="solid")
        map_card.pack(fill="x", pady=(0, 12))
        ttk.Label(map_card, text="Column mapping", style="H2.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        choices = ["(none)"] + self.columns
        self.map_vars = {}
        for i, (field, label, required) in enumerate(LOGICAL_FIELDS, start=1):
            text = label + ("  *" if required else "")
            tk.Label(map_card, text=text, bg=COLORS["card"], fg=COLORS["text"],
                     font=FONTS["body"]).grid(row=i, column=0, sticky="w", pady=3, padx=(0, 12))
            var = tk.StringVar(value=mapping.get(field, "(none)") or "(none)")
            cb = ttk.Combobox(map_card, textvariable=var, values=choices, state="readonly", width=40)
            cb.grid(row=i, column=1, sticky="w", pady=3)
            if field == "status":
                cb.bind("<<ComboboxSelected>>", lambda e: self._refresh_statuses())
            self.map_vars[field] = var
        tk.Label(map_card, text="*  required", bg=COLORS["card"], fg=COLORS["muted"],
                 font=FONTS["kpi_label"]).grid(row=len(LOGICAL_FIELDS) + 1, column=0, sticky="w", pady=(8, 0))

        # --- pending status panel ---
        self.status_card = ttk.Frame(self.body, style="Card.TFrame", padding=16)
        self.status_card.configure(borderwidth=1, relief="solid")
        self.status_card.pack(fill="x", pady=(0, 12))
        ttk.Label(self.status_card, text="Which statuses are 'pending to send'?",
                  style="H2.TLabel").pack(anchor="w", pady=(0, 8))
        self.status_box = ttk.Frame(self.status_card, style="Card.TFrame")
        self.status_box.pack(fill="x")
        self._refresh_statuses()

        ttk.Button(self.body, text="Import now", style="Accent.TButton",
                   command=self._do_import).pack(anchor="w", pady=(4, 0))

    def _current_mapping(self) -> dict:
        return {f: v.get() for f, v in self.map_vars.items() if v.get() and v.get() != "(none)"}

    def _refresh_statuses(self):
        for w in self.status_box.winfo_children():
            w.destroy()
        self.status_vars = {}

        mapping = self._current_mapping()
        if "status" not in mapping or self.df is None:
            tk.Label(self.status_box, text="Map a Status column above to choose pending statuses.",
                     bg=COLORS["card"], fg=COLORS["muted"], font=FONTS["body"]).pack(anchor="w")
            return

        records = importer.apply_mapping(self.df, mapping)
        statuses = importer.distinct_statuses(records)
        saved_pending = set(s.lower() for s in self.app.pending_statuses)

        for i, status in enumerate(statuses):
            if saved_pending:
                checked = status.lower() in saved_pending
            else:
                checked = not any(h in status.lower() for h in _DONE_HINTS)
            var = tk.BooleanVar(value=checked)
            counts = sum(1 for r in records if r["status"] == status)
            cb = tk.Checkbutton(self.status_box, text=f"{status}  ({counts})", variable=var,
                                bg=COLORS["card"], fg=COLORS["text"], font=FONTS["body"],
                                activebackground=COLORS["card"], selectcolor="white", anchor="w")
            cb.grid(row=i // 3, column=i % 3, sticky="w", padx=4, pady=2)
            self.status_vars[status] = var

    # ---- run the import -------------------------------------------------
    def _do_import(self):
        mapping = self._current_mapping()
        missing = [f for f in REQUIRED_FIELDS if f not in mapping]
        if missing:
            messagebox.showwarning("Missing required columns",
                                   "Please map these required fields:\n\n" + "\n".join(missing))
            return
        pending = [s for s, v in self.status_vars.items() if v.get()]
        if not pending:
            if not messagebox.askyesno("No statuses selected",
                                       "No statuses are marked as pending, so every row will be "
                                       "treated as pending. Continue?"):
                return

        # Persist mapping + status choice so next import is one click.
        self.app.store.save_mapping(self.signature, mapping, self.app.today())
        self.app.set_pending_statuses(pending)

        try:
            stats = service.import_csv(self.app.store, self.path, mapping, pending, self.app.today())
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Import failed", str(exc))
            return

        self.app.recompute()
        messagebox.showinfo(
            "Import complete",
            f"Imported {stats['rows']} rows.\n\n"
            f"Pending items: {stats['pending']}\n"
            f"New: {stats['new']}   Updated: {stats['updated']}   "
            f"Resolved (no longer pending): {stats['resolved']}",
        )
        self.app.navigate("overview")

    def on_data(self):
        pass  # import view doesn't react to recompute
