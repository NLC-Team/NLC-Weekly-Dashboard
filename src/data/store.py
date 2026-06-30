"""SQLite persistence: settings, saved column mappings, and item history.

History tracking is what lets the dashboard answer "how long has this been
open?" even when the CSV has no useful date. Every import upserts the pending
items; an item keeps its original `first_seen`, and anything that stops
appearing is marked resolved (i.e. it was sent/completed).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS mappings (
    signature  TEXT PRIMARY KEY,
    mapping    TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS items (
    item_key    TEXT PRIMARY KEY,
    assignee    TEXT,
    client      TEXT,
    title       TEXT,
    last_status TEXT,
    source_date TEXT,
    first_seen  TEXT,
    last_seen   TEXT,
    resolved    INTEGER DEFAULT 0,
    resolved_at TEXT,
    project_key TEXT,
    return_type_raw TEXT
);
CREATE TABLE IF NOT EXISTS imports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    imported_at  TEXT,
    file_name    TEXT,
    row_count    INTEGER,
    pending_count INTEGER
);
-- Per-document "received from client" checkbox state.
CREATE TABLE IF NOT EXISTS doc_state (
    item_key    TEXT PRIMARY KEY,
    received    INTEGER DEFAULT 0,
    received_at TEXT
);
-- Per-project return type (Individual/Business) and manual completion.
CREATE TABLE IF NOT EXISTS project_state (
    project_key  TEXT PRIMARY KEY,
    return_type  TEXT,
    completed    INTEGER DEFAULT 0,
    completed_at TEXT
);
-- Staff directory: names and their access roles.
CREATE TABLE IF NOT EXISTS staff (
    name     TEXT PRIMARY KEY,
    role     TEXT DEFAULT 'Viewer',
    added_at TEXT
);
"""

# Columns added after the first release; applied to older databases on open.
_MIGRATIONS = {
    "items": [("project_key", "TEXT"), ("return_type_raw", "TEXT")],
}


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _to_date(s) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


class Store:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else config.default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        """Add any columns introduced after a database was first created."""
        for table, cols in _MIGRATIONS.items():
            existing = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in cols:
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def close(self):
        self.conn.close()

    # ---- settings -------------------------------------------------------
    def get_setting(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return default

    def set_setting(self, key: str, value) -> None:
        self.conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
        self.conn.commit()

    # ---- column mappings ------------------------------------------------
    def get_mapping(self, signature: str) -> dict | None:
        row = self.conn.execute(
            "SELECT mapping FROM mappings WHERE signature=?", (signature,)
        ).fetchone()
        return json.loads(row["mapping"]) if row else None

    def save_mapping(self, signature: str, mapping: dict, today: date) -> None:
        self.conn.execute(
            "INSERT INTO mappings(signature, mapping, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(signature) DO UPDATE SET mapping=excluded.mapping, "
            "updated_at=excluded.updated_at",
            (signature, json.dumps(mapping), today.isoformat()),
        )
        self.conn.commit()

    # ---- import / history ----------------------------------------------
    def record_import(self, file_name: str, row_count: int, pending_count: int, today: date) -> int:
        cur = self.conn.execute(
            "INSERT INTO imports(imported_at, file_name, row_count, pending_count) "
            "VALUES(?, ?, ?, ?)",
            (today.isoformat(), file_name, row_count, pending_count),
        )
        self.conn.commit()
        return cur.lastrowid

    def upsert_items(self, pending_records: list[dict], today: date) -> dict:
        """Insert/update pending items and mark vanished ones as resolved.

        Returns a small stats dict: {new, updated, resolved}.
        """
        iso = today.isoformat()
        new = updated = 0
        for rec in pending_records:
            existing = self.conn.execute(
                "SELECT item_key, first_seen FROM items WHERE item_key=?",
                (rec["item_key"],),
            ).fetchone()
            if existing is None:
                # first_seen uses the real source date when available, else today.
                first_seen = _iso(rec.get("source_date")) or iso
                self.conn.execute(
                    "INSERT INTO items(item_key, assignee, client, title, last_status, "
                    "source_date, first_seen, last_seen, resolved, resolved_at, "
                    "project_key, return_type_raw) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)",
                    (
                        rec["item_key"], rec["assignee"], rec["client"], rec["title"],
                        rec["status"], _iso(rec.get("source_date")), first_seen, iso,
                        rec.get("project_key"), rec.get("return_type_raw"),
                    ),
                )
                new += 1
            else:
                self.conn.execute(
                    "UPDATE items SET assignee=?, client=?, title=?, last_status=?, "
                    "source_date=COALESCE(?, source_date), last_seen=?, resolved=0, "
                    "resolved_at=NULL, project_key=?, return_type_raw=? WHERE item_key=?",
                    (
                        rec["assignee"], rec["client"], rec["title"], rec["status"],
                        _iso(rec.get("source_date")), iso,
                        rec.get("project_key"), rec.get("return_type_raw"), rec["item_key"],
                    ),
                )
                updated += 1

        # Anything still marked active but not seen in this import is now resolved.
        cur = self.conn.execute(
            "UPDATE items SET resolved=1, resolved_at=? WHERE resolved=0 AND last_seen<>?",
            (iso, iso),
        )
        resolved = cur.rowcount
        self.conn.commit()
        return {"new": new, "updated": updated, "resolved": resolved}

    def active_items(self) -> list[dict]:
        """Current pending items (latest import, not resolved), with history.

        first_seen is returned as a date so analytics can compute age directly.
        """
        rows = self.conn.execute(
            "SELECT * FROM items WHERE resolved=0 ORDER BY first_seen ASC"
        ).fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "item_key": r["item_key"],
                    "assignee": r["assignee"],
                    "client": r["client"],
                    "title": r["title"],
                    "status": r["last_status"],
                    "source_date": _to_date(r["source_date"]),
                    "first_seen": _to_date(r["first_seen"]),
                    "project_key": r["project_key"] or ("c:" + (r["client"] or "").strip().lower()),
                    "return_type_raw": r["return_type_raw"],
                }
            )
        return out

    def last_import(self) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM imports ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    # ---- document received state ---------------------------------------
    def get_doc_states(self) -> dict:
        """Map of item_key -> True/False for 'received from client'."""
        rows = self.conn.execute("SELECT item_key, received FROM doc_state").fetchall()
        return {r["item_key"]: bool(r["received"]) for r in rows}

    def set_received(self, item_key: str, received: bool, today: date) -> None:
        self.conn.execute(
            "INSERT INTO doc_state(item_key, received, received_at) VALUES(?, ?, ?) "
            "ON CONFLICT(item_key) DO UPDATE SET received=excluded.received, "
            "received_at=excluded.received_at",
            (item_key, 1 if received else 0, today.isoformat() if received else None),
        )
        self.conn.commit()

    # ---- project state (return type + completion) ----------------------
    def get_project_states(self) -> dict:
        """Map of project_key -> {return_type, completed}."""
        rows = self.conn.execute(
            "SELECT project_key, return_type, completed FROM project_state"
        ).fetchall()
        return {
            r["project_key"]: {"return_type": r["return_type"], "completed": bool(r["completed"])}
            for r in rows
        }

    def set_project_type(self, project_key: str, return_type: str) -> None:
        self.conn.execute(
            "INSERT INTO project_state(project_key, return_type) VALUES(?, ?) "
            "ON CONFLICT(project_key) DO UPDATE SET return_type=excluded.return_type",
            (project_key, return_type),
        )
        self.conn.commit()

    def set_project_completed(self, project_key: str, completed: bool, today: date) -> None:
        self.conn.execute(
            "INSERT INTO project_state(project_key, completed, completed_at) VALUES(?, ?, ?) "
            "ON CONFLICT(project_key) DO UPDATE SET completed=excluded.completed, "
            "completed_at=excluded.completed_at",
            (project_key, 1 if completed else 0, today.isoformat() if completed else None),
        )
        self.conn.commit()

    # ---- staff directory -----------------------------------------------
    def get_staff(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT name, role FROM staff ORDER BY name"
        ).fetchall()
        return [{"name": r["name"], "role": r["role"]} for r in rows]

    def upsert_staff(self, name: str, role: str, today: date | None = None) -> None:
        added = today.isoformat() if today else None
        self.conn.execute(
            "INSERT INTO staff(name, role, added_at) VALUES(?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET role=excluded.role",
            (name, role, added),
        )
        self.conn.commit()

    def remove_staff(self, name: str) -> None:
        self.conn.execute("DELETE FROM staff WHERE name=?", (name,))
        self.conn.commit()

    # ---- delete a client / project entirely ----------------------------
    def delete_project(self, project_key: str, item_keys: list[str]) -> None:
        """Permanently remove a client/project: its items, document state,
        and project state. Frees the space so it no longer appears anywhere.
        """
        for ik in item_keys:
            self.conn.execute("DELETE FROM items WHERE item_key=?", (ik,))
            self.conn.execute("DELETE FROM doc_state WHERE item_key=?", (ik,))
        self.conn.execute("DELETE FROM project_state WHERE project_key=?", (project_key,))
        self.conn.commit()
