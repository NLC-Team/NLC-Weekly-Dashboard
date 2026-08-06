"""SQLite persistence: settings, saved column mappings, and item history.

History tracking is what lets the dashboard answer "how long has this been
open?" even when the export has no useful date.

`first_seen` IS NOT IMPORT PROVENANCE, despite the name. It holds the
document's own start date from the export (`source_date`); only when a row has
no start date does it fall back to the date of the import that stored it (see
upsert_items). Real Karbon exports always carry a start date, so in practice it
equals `source_date` -- it can even be a FUTURE date, and it is set once at
insert and never revised, so a later export changing a row's start date leaves
`first_seen` on the old value. Nothing on the items table records which import
created a row; the per-import "N new, M updated" counts live in `audit_log`.

Imports are ADDITIVE: an item missing from a later export is left untouched and
stays active, so a partial file never wipes earlier data. The `resolved` column
is vestigial -- `active_items` still filters on `resolved=0`, but nothing ever
sets it to 1; work is removed by a hard delete instead (see delete_project).
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from datetime import date, timedelta
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
    client_owner TEXT,
    title       TEXT,
    last_status TEXT,
    source_date TEXT,
    due_date    TEXT,
    completed_date TEXT,
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
-- Staff directory + login accounts: names, access roles, and credentials.
-- username is the unique login handle; password_hash is a Werkzeug hash;
-- status is 'active' (can log in) or 'pending' (awaiting admin approval).
CREATE TABLE IF NOT EXISTS staff (
    name          TEXT PRIMARY KEY,
    role          TEXT DEFAULT 'Viewer',
    added_at      TEXT,
    username      TEXT,
    password_hash TEXT,
    status        TEXT DEFAULT 'active',
    email         TEXT
);
-- The dashboard reads active items on every page load via
-- "WHERE resolved=0"; index it so that stays fast as resolved history grows.
CREATE INDEX IF NOT EXISTS idx_items_resolved ON items(resolved);
-- Security audit trail: who did what, from where, when. Append-only; the app
-- never updates or deletes rows here.
CREATE TABLE IF NOT EXISTS audit_log (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT,
    actor  TEXT,
    ip     TEXT,
    action TEXT,
    detail TEXT
);
-- Weekly rollover of the audit trail: the live audit_log only holds the current
-- week; older events are MOVED here (never deleted) so the live view stays short
-- while the full history is preserved for admins, partitioned by ISO week.
CREATE TABLE IF NOT EXISTS audit_archive (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT,
    actor  TEXT,
    ip     TEXT,
    action TEXT,
    detail TEXT,
    week   TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_archive_week ON audit_archive(week);
"""

# Columns added after the first release; applied to older databases on open.
_MIGRATIONS = {
    "items": [("project_key", "TEXT"), ("return_type_raw", "TEXT"),
              ("client_owner", "TEXT"), ("due_date", "TEXT"),
              ("completed_date", "TEXT")],
    "staff": [("username", "TEXT"), ("password_hash", "TEXT"),
              ("status", "TEXT DEFAULT 'active'"), ("email", "TEXT"),
              ("email_verified", "INTEGER DEFAULT 0"),
              ("verify_code", "TEXT"), ("verify_sent_at", "TEXT"),
              # Bumped whenever credentials change; sessions carry the value
              # they were issued with and die when it no longer matches.
              ("session_rev", "INTEGER DEFAULT 0")],
    # 'manual' (set via the Done/Open button) or 'import' (set by the import
    # auto-complete sync). Lets the sync leave human decisions alone. See
    # apply_import_completion / set_project_completed.
    "project_state": [("completed_source", "TEXT")],
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
        # Each thread gets its OWN connection (see the `conn` property). A single
        # connection shared across Waitress's worker threads is NOT safe: concurrent
        # use corrupts cursor/result state — observed under load as SELECT COUNT(*)
        # returning no row, "bad parameter or other API misuse", and stray
        # IndexErrors. Per-thread connections + WAL let many readers and the single
        # writer run without stepping on each other.
        self._local = threading.local()
        # Serialises multi-statement writes (archive-then-delete, import batches)
        # so their steps commit as a unit and never interleave with another write.
        self._write_lock = threading.RLock()
        conn = self.conn                # opens this (init) thread's connection
        conn.executescript(SCHEMA)
        self._migrate()
        conn.commit()
        # Bumped on every write that changes what the dashboard shows, so the
        # web layer can cache computed dashboard data and only recompute when
        # this number moves. Starts at 1 so a fresh (uncached) key never matches.
        self._data_version = 1

    def _new_conn(self) -> sqlite3.Connection:
        """Open a fresh connection tuned for shared, multi-threaded use."""
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=5.0)
        conn.row_factory = sqlite3.Row
        #   - WAL: readers keep reading while the one writer writes (no whole-file
        #     lock) — the dashboard is read-heavy with occasional writes.
        #   - busy_timeout: a writer that finds the DB momentarily locked waits up
        #     to 5s and retries instead of instantly raising "database is locked".
        #   - synchronous=NORMAL: the safe, faster durability level to pair with WAL.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        """This thread's own SQLite connection (created on first use)."""
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self._new_conn()
            self._local.conn = c
        return c

    @property
    def data_version(self) -> int:
        """Monotonic counter that increments on any dashboard-affecting write."""
        return self._data_version

    def _bump(self) -> None:
        self._data_version += 1

    def _migrate(self):
        """Add any columns introduced after a database was first created."""
        for table, cols in _MIGRATIONS.items():
            existing = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            newly_added = []
            for name, decl in cols:
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                    newly_added.append(name)
            # When email verification is first introduced, accounts that already
            # existed were already trusted — treat their email as verified so the
            # switch to email login doesn't lock anyone out.
            if table == "staff" and "email_verified" in newly_added:
                self.conn.execute("UPDATE staff SET email_verified=1")
        # Login handles must be unique. Created here (not in SCHEMA) so they run
        # after the columns are guaranteed to exist on older databases.
        # Partial indexes: legacy rows with NULL username/email don't collide.
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_staff_username "
            "ON staff(username) WHERE username IS NOT NULL"
        )
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_staff_email "
            "ON staff(email) WHERE email IS NOT NULL"
        )

    def close(self):
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None

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
        """Insert new items and update existing ones (matched by item_key).

        Imports are ADDITIVE: items from earlier imports are kept even when a
        new file doesn't contain them, so importing a different/partial export
        (e.g. a bookkeeping list) never wipes previously-loaded data. Remove
        items you no longer want with the Delete-client / delete-return tools.

        Returns a small stats dict: {new, updated}.

        Existing keys are fetched once up front and inserts/updates are batched
        with executemany, so an N-row import runs a handful of statements rather
        than 1-2 queries per row.
        """
        iso = today.isoformat()
        # Whole import runs under the write lock: the read of existing keys and
        # the batched insert/update must not interleave with another thread's
        # write on the shared connection.
        with self._write_lock:
            existing = {
                r["item_key"] for r in self.conn.execute("SELECT item_key FROM items")
            }

            inserts: list[tuple] = []
            updates: list[tuple] = []
            # Guard against a key appearing twice in ONE batch (e.g. duplicate rows
            # in the export): the second occurrence becomes an UPDATE, never a second
            # INSERT — so executemany can't hit a UNIQUE collision. apply_mapping
            # already disambiguates dup keys, but this keeps the store safe on its own.
            batch_new: set[str] = set()
            for rec in pending_records:
                # Empty owner -> NULL so a re-import without the column mapped keeps
                # any owner already on file (see COALESCE in the UPDATE below).
                client_owner = (rec.get("client_owner") or "").strip() or None
                if rec["item_key"] not in existing and rec["item_key"] not in batch_new:
                    batch_new.add(rec["item_key"])
                    # first_seen uses the real source date when available, else today.
                    first_seen = _iso(rec.get("source_date")) or iso
                    inserts.append((
                        rec["item_key"], rec["assignee"], rec["client"], client_owner,
                        rec["title"], rec["status"], _iso(rec.get("source_date")),
                        _iso(rec.get("due_date")), _iso(rec.get("completed_date")),
                        first_seen, iso, rec.get("project_key"), rec.get("return_type_raw"),
                    ))
                else:
                    updates.append((
                        rec["assignee"], rec["client"], client_owner, rec["title"],
                        rec["status"], _iso(rec.get("source_date")), _iso(rec.get("due_date")),
                        _iso(rec.get("completed_date")), iso,
                        rec.get("project_key"), rec.get("return_type_raw"), rec["item_key"],
                    ))

            if inserts:
                self.conn.executemany(
                    "INSERT INTO items(item_key, assignee, client, client_owner, title, "
                    "last_status, source_date, due_date, completed_date, "
                    "first_seen, last_seen, resolved, resolved_at, "
                    "project_key, return_type_raw) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)",
                    inserts,
                )
            if updates:
                # completed_date is COALESCEd like the other dates: a re-import from
                # a file that doesn't map the column leaves the known completion
                # date alone rather than wiping it. A real new value still wins, so
                # a corrected date in Karbon does flow through.
                self.conn.executemany(
                    "UPDATE items SET assignee=?, client=?, "
                    "client_owner=COALESCE(?, client_owner), title=?, last_status=?, "
                    "source_date=COALESCE(?, source_date), due_date=COALESCE(?, due_date), "
                    "completed_date=COALESCE(?, completed_date), "
                    "last_seen=?, resolved=0, "
                    "resolved_at=NULL, project_key=?, return_type_raw=? WHERE item_key=?",
                    updates,
                )

            # Additive by design: items absent from this import are left untouched
            # (still active), so a new or partial file never wipes prior data.
            self.conn.commit()
            self._bump()
            # `new_keys` lets the import auto-complete sync tell which documents
            # are brand-new (so new work can reopen a hand-completed client).
            return {"new": len(inserts), "updated": len(updates),
                    "new_keys": [i[0] for i in inserts]}

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
                    "client_owner": r["client_owner"],
                    "title": r["title"],
                    "status": r["last_status"],
                    "source_date": _to_date(r["source_date"]),
                    "due_date": _to_date(r["due_date"]),
                    "completed_date": _to_date(r["completed_date"]),
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

    def project_key_for_item(self, item_key: str) -> str | None:
        """The effective project_key an item belongs to, mirroring how
        analytics.build_projects groups items: the item's own project_key, else a
        synthetic 'c:'+client key. Used to reopen a client when a document of
        theirs is marked received."""
        row = self.conn.execute(
            "SELECT project_key, client FROM items WHERE item_key=?", (item_key,)
        ).fetchone()
        if not row:
            return None
        return row["project_key"] or ("c:" + (row["client"] or "").strip().lower())

    def set_received(self, item_key: str, received: bool, today: date) -> None:
        self.conn.execute(
            "INSERT INTO doc_state(item_key, received, received_at) VALUES(?, ?, ?) "
            "ON CONFLICT(item_key) DO UPDATE SET received=excluded.received, "
            "received_at=excluded.received_at",
            (item_key, 1 if received else 0, today.isoformat() if received else None),
        )
        # Marking a document received reclassifies its client as OPEN: activity on
        # a client means it's being worked, so a client that was "closed"
        # (completed) is automatically reopened. Unticking never auto-closes.
        if received:
            pkey = self.project_key_for_item(item_key)
            if pkey:
                cur = self.conn.execute(
                    "SELECT completed FROM project_state WHERE project_key=?", (pkey,)
                ).fetchone()
                if cur and cur["completed"]:
                    self.conn.execute(
                        "UPDATE project_state SET completed=0, completed_at=NULL "
                        "WHERE project_key=?", (pkey,)
                    )
        self.conn.commit()
        self._bump()

    # ---- project state (return type + completion) ----------------------
    def get_project_states(self) -> dict:
        """Map of project_key -> {return_type, completed, completed_at, completed_source}."""
        rows = self.conn.execute(
            "SELECT project_key, return_type, completed, completed_at, completed_source "
            "FROM project_state"
        ).fetchall()
        return {
            r["project_key"]: {
                "return_type": r["return_type"],
                "completed": bool(r["completed"]),
                "completed_at": r["completed_at"],
                "completed_source": r["completed_source"],
            }
            for r in rows
        }

    def set_project_type(self, project_key: str, return_type: str) -> None:
        self.conn.execute(
            "INSERT INTO project_state(project_key, return_type) VALUES(?, ?) "
            "ON CONFLICT(project_key) DO UPDATE SET return_type=excluded.return_type",
            (project_key, return_type),
        )
        self.conn.commit()
        self._bump()

    def set_project_completed(self, project_key: str, completed: bool, today: date,
                              source: str = "manual") -> None:
        """Set a return's completed flag. `source` records who set it — 'manual'
        (the Done/Open button, the default) or 'import' (the auto-complete sync) —
        so the sync can leave hand-made decisions alone (see apply_import_completion)."""
        self.conn.execute(
            "INSERT INTO project_state(project_key, completed, completed_at, completed_source) "
            "VALUES(?, ?, ?, ?) "
            "ON CONFLICT(project_key) DO UPDATE SET completed=excluded.completed, "
            "completed_at=excluded.completed_at, completed_source=excluded.completed_source",
            (project_key, 1 if completed else 0,
             today.isoformat() if completed else None, source),
        )
        self.conn.commit()
        self._bump()

    def apply_import_completion(self, completed_statuses, new_keys, today: date) -> dict:
        """Auto-complete / reopen returns from the just-imported statuses.

        Rules (see the feature design):
          - A return auto-completes when ALL its active documents carry a status
            in `completed_statuses`. It's stamped completed_at=today, source='import'.
          - Kept in sync: an import that leaves any active document NOT completed
            reopens a return that the import had completed.
          - Manual completions (Done button, source='manual') are left alone —
            EXCEPT they're reopened when this import ADDS a new active (non-completed)
            document to that client (new work arrived; it's not finished after all).

        `new_keys` = item_keys inserted by this import (from upsert_items). Returns
        {completed, reopened} counts. No-op when no completed statuses are configured.
        """
        wanted = {s.strip().lower() for s in (completed_statuses or [])}
        if not wanted:
            return {"completed": 0, "reopened": 0}
        new_keys = set(new_keys or [])

        def _done(status) -> bool:
            return (status or "").strip().lower() in wanted

        with self._write_lock:
            rows = self.conn.execute(
                "SELECT item_key, last_status, project_key, client FROM items WHERE resolved=0"
            ).fetchall()
            by_pkey: dict[str, list] = {}
            for r in rows:
                pkey = r["project_key"] or ("c:" + (r["client"] or "").strip().lower())
                by_pkey.setdefault(pkey, []).append(r)

            states = {
                r["project_key"]: r for r in self.conn.execute(
                    "SELECT project_key, completed, completed_source FROM project_state"
                ).fetchall()
            }

            def _write(pkey, completed, source):
                self.conn.execute(
                    "INSERT INTO project_state(project_key, completed, completed_at, "
                    "completed_source) VALUES(?, ?, ?, ?) "
                    "ON CONFLICT(project_key) DO UPDATE SET completed=excluded.completed, "
                    "completed_at=excluded.completed_at, completed_source=excluded.completed_source",
                    (pkey, completed, today.isoformat() if completed else None, source),
                )

            completed_n = reopened_n = 0
            for pkey, docs in by_pkey.items():
                all_done = bool(docs) and all(_done(d["last_status"]) for d in docs)
                has_new_active = any(
                    d["item_key"] in new_keys and not _done(d["last_status"]) for d in docs
                )
                st = states.get(pkey)
                completed = bool(st["completed"]) if st else False
                source = (st["completed_source"] if st else None)

                if completed and source == "manual":
                    if has_new_active:               # new work on a hand-completed client
                        _write(pkey, 0, None)
                        reopened_n += 1
                elif all_done:
                    if not completed:
                        _write(pkey, 1, "import")
                        completed_n += 1
                elif completed and source != "manual":  # import had completed it; no longer all done
                    _write(pkey, 0, None)
                    reopened_n += 1

            self.conn.commit()
            self._bump()
            return {"completed": completed_n, "reopened": reopened_n}

    # ---- staff directory + login accounts ------------------------------
    def get_staff(self) -> list[dict]:
        """All active staff (the directory). Excludes pending sign-ups."""
        rows = self.conn.execute(
            "SELECT name, role, username, email, status, password_hash, "
            "COALESCE(email_verified,0) AS email_verified FROM staff "
            "WHERE status='active' OR status IS NULL ORDER BY name"
        ).fetchall()
        return [
            {"name": r["name"], "role": r["role"], "username": r["username"],
             "email": r["email"], "status": r["status"] or "active",
             "email_verified": bool(r["email_verified"]),
             "has_login": bool(r["password_hash"])}
            for r in rows
        ]

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

    # ---- login accounts -------------------------------------------------
    # Accounts are managed by `name` (the primary key) and authenticated by
    # `email` (the login handle; `username` remains as a legacy fallback).
    _ACCOUNT_COLS = ("name, role, username, email, status, password_hash, "
                     "COALESCE(email_verified,0) AS email_verified, verify_code, verify_sent_at, "
                     "COALESCE(session_rev,0) AS session_rev")

    @staticmethod
    def _account(r) -> dict | None:
        if not r:
            return None
        return {
            "name": r["name"], "role": r["role"], "username": r["username"],
            "email": r["email"], "status": r["status"] or "active",
            "password_hash": r["password_hash"],
            "email_verified": bool(r["email_verified"]),
            "verify_code": r["verify_code"], "verify_sent_at": r["verify_sent_at"],
            "session_rev": r["session_rev"],
            "has_login": bool(r["password_hash"]),
        }

    def get_account_by_name(self, name: str) -> dict | None:
        if not name:
            return None
        return self._account(self.conn.execute(
            f"SELECT {self._ACCOUNT_COLS} FROM staff WHERE name=?", (name,)).fetchone())

    def get_account_by_email(self, email: str) -> dict | None:
        if not email:
            return None
        return self._account(self.conn.execute(
            f"SELECT {self._ACCOUNT_COLS} FROM staff WHERE email=? COLLATE NOCASE",
            (email,)).fetchone())

    def get_account_by_username(self, username: str) -> dict | None:
        if not username:
            return None
        return self._account(self.conn.execute(
            f"SELECT {self._ACCOUNT_COLS} FROM staff WHERE username=? COLLATE NOCASE",
            (username,)).fetchone())

    def get_account_by_login(self, login: str) -> dict | None:
        """Authenticate lookup: email first, then username (legacy fallback)."""
        if not login:
            return None
        acct = self.get_account_by_email(login)
        if acct:
            return acct
        return self._account(self.conn.execute(
            f"SELECT {self._ACCOUNT_COLS} FROM staff WHERE username=?", (login,)).fetchone())

    def create_account(self, name: str, role: str, password_hash: str, status: str, *,
                       email: str | None = None, username: str | None = None,
                       email_verified: int = 0, verify_code: str | None = None,
                       verify_sent_at: str | None = None, today: date | None = None) -> None:
        """Create a login account. Raises sqlite3.IntegrityError if the display
        name (primary key), username or email is already in use.

        The account starts with a RANDOM session_rev, not 0. Sessions authenticate
        on (name, session_rev); if a deleted staff member is later re-created with
        the same name, a fresh random rev guarantees their old pre-deletion cookie
        can't match the new account and silently log them back in — they must sign
        in again. (A deleted row's rev is gone, so we can't just increment it.)"""
        self.conn.execute(
            "INSERT INTO staff(name, role, added_at, username, email, password_hash, "
            "status, email_verified, verify_code, verify_sent_at, session_rev) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, role, today.isoformat() if today else None, username, email,
             password_hash, status, 1 if email_verified else 0, verify_code, verify_sent_at,
             secrets.randbelow(2_000_000_000)),
        )
        self.conn.commit()

    def set_login(self, name: str, email: str, password_hash: str) -> None:
        """Set a member's email login + password and activate them (admin action
        for legacy rows / resets). Marks the email verified since an admin set it.
        Bumps session_rev so any sessions issued under the old credentials die."""
        self.conn.execute(
            "UPDATE staff SET email=?, password_hash=?, status='active', email_verified=1, "
            "session_rev=COALESCE(session_rev,0)+1 WHERE name=?",
            (email, password_hash, name),
        )
        self.conn.commit()

    def set_password(self, name: str, password_hash: str) -> None:
        """Change a password. Bumps session_rev: every existing session for the
        account is invalidated (the caller re-stamps its own session to stay in)."""
        self.conn.execute(
            "UPDATE staff SET password_hash=?, session_rev=COALESCE(session_rev,0)+1 "
            "WHERE name=?", (password_hash, name))
        self.conn.commit()

    def set_email(self, name: str, email: str) -> None:
        """Set/replace a logged-in user's own email (treated as verified)."""
        self.conn.execute(
            "UPDATE staff SET email=?, email_verified=1 WHERE name=?", (email, name))
        self.conn.commit()

    def set_verify_code(self, name: str, code: str, sent_at: str) -> None:
        self.conn.execute(
            "UPDATE staff SET verify_code=?, verify_sent_at=? WHERE name=?",
            (code, sent_at, name))
        self.conn.commit()

    def mark_email_verified(self, name: str) -> None:
        self.conn.execute(
            "UPDATE staff SET email_verified=1, verify_code=NULL WHERE name=?", (name,))
        self.conn.commit()

    def approve_account(self, name: str, role: str) -> None:
        self.conn.execute(
            "UPDATE staff SET status='active', role=? WHERE name=?", (role, name))
        self.conn.commit()

    def list_pending(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT name, role, username, email, COALESCE(email_verified,0) AS email_verified "
            "FROM staff WHERE status='pending' ORDER BY added_at"
        ).fetchall()
        return [{"name": r["name"], "role": r["role"], "username": r["username"],
                 "email": r["email"], "email_verified": bool(r["email_verified"])}
                for r in rows]

    def count_active_admins(self) -> int:
        """Admins who can actually sign in (active + have a password)."""
        r = self.conn.execute(
            "SELECT COUNT(*) FROM staff WHERE role='Admin' AND status='active' "
            "AND password_hash IS NOT NULL AND password_hash != ''"
        ).fetchone()
        return r[0]

    # ---- audit trail ------------------------------------------------------
    def log_event(self, ts: str, actor: str, ip: str, action: str, detail: str = "") -> None:
        """Append one security-relevant event. Never raises: an audit failure
        must not take down the action being audited."""
        try:
            self.conn.execute(
                "INSERT INTO audit_log(ts, actor, ip, action, detail) VALUES(?, ?, ?, ?, ?)",
                (ts, actor, ip, action, detail))
            self.conn.commit()
        except sqlite3.Error:
            pass

    def recent_events(self, limit: int = 200) -> list[dict]:
        rows = self.conn.execute(
            "SELECT ts, actor, ip, action, detail FROM audit_log "
            "ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _week_label(d: date) -> str:
        """ISO year + week, e.g. '2026-W28' — sorts chronologically as a string."""
        iso = d.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"

    def rotate_audit(self, today: date) -> int:
        """Move every audit event from before the current ISO week out of the live
        log and into audit_archive (tagged by the week it happened). The live log
        is left holding only this week's events. Nothing is destroyed — the archive
        keeps the full history. Idempotent: running it twice in a week is a no-op.
        Returns how many events were archived."""
        monday = today - timedelta(days=today.weekday())  # Monday 00:00 this week
        cutoff = monday.isoformat()
        with self._write_lock:
            rows = self.conn.execute(
                "SELECT ts, actor, ip, action, detail FROM audit_log WHERE ts < ?",
                (cutoff,)).fetchall()
            if not rows:
                return 0
            payload = []
            for r in rows:
                d = _to_date((r["ts"] or "")[:10]) or monday
                payload.append((r["ts"], r["actor"], r["ip"], r["action"],
                                r["detail"], self._week_label(d)))
            self.conn.executemany(
                "INSERT INTO audit_archive(ts, actor, ip, action, detail, week) "
                "VALUES(?, ?, ?, ?, ?, ?)", payload)
            self.conn.execute("DELETE FROM audit_log WHERE ts < ?", (cutoff,))
            self.conn.commit()
            return len(rows)

    def archived_weeks(self) -> list[dict]:
        """Each archived ISO week (newest first) with its event count and span.

        `num` is a running week number that reads plainly for people: the
        earliest archived week is Week 1 and each later week counts up from
        there, independent of the ISO calendar code."""
        rows = self.conn.execute(
            "SELECT week, COUNT(*) AS n, MIN(ts) AS first_ts, MAX(ts) AS last_ts "
            "FROM audit_archive GROUP BY week ORDER BY week DESC").fetchall()
        total = len(rows)
        return [{"week": r["week"], "count": r["n"], "num": total - i,
                 "first_ts": r["first_ts"], "last_ts": r["last_ts"]}
                for i, r in enumerate(rows)]

    def archived_events(self, week: str, limit: int = 5000) -> list[dict]:
        """All events in one archived week, newest first."""
        rows = self.conn.execute(
            "SELECT ts, actor, ip, action, detail FROM audit_archive "
            "WHERE week=? ORDER BY id DESC LIMIT ?", (week, int(limit))).fetchall()
        return [dict(r) for r in rows]

    # ---- delete a client / project entirely ----------------------------
    def delete_project(self, project_key: str, item_keys: list[str]) -> None:
        """Permanently remove a client/project: its items, document state,
        and project state. Frees the space so it no longer appears anywhere.
        """
        with self._write_lock:
            for ik in item_keys:
                self.conn.execute("DELETE FROM items WHERE item_key=?", (ik,))
                self.conn.execute("DELETE FROM doc_state WHERE item_key=?", (ik,))
            self.conn.execute("DELETE FROM project_state WHERE project_key=?", (project_key,))
            self.conn.commit()
            self._bump()
