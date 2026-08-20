"""Application configuration: paths, logical fields, and default settings.

Everything that the rest of the app needs to know about *where* data lives and
*what* the sensible defaults are is centralised here so it can be changed in one
place (and so the packaged .exe behaves predictably).
"""
from __future__ import annotations

import functools
import json
import os
import re
import secrets
from pathlib import Path

APP_NAME = "Karbon Pending Dashboard"

# --- Firm-specific rules (kept OUT of this repository) ----------------------
# Three of the dashboard's rules are keyed on real names — a staff member whose
# property line-items are hidden, internal/test clients that are excluded, and
# former staff whose work is relabelled "Unassigned". Naming real employees and
# clients in a public repo isn't acceptable, so those values live in an
# untracked `local_config.py` beside this file.
#
# To set them up: copy `local_config.example.py` to `local_config.py` and fill in
# your own values. Without that file the dashboard still runs perfectly — it just
# applies none of these three rules, so nothing is hidden, excluded or relabelled.
try:
    import local_config as _local
except ModuleNotFoundError:
    _local = None


def _firm_setting(name, default):
    return getattr(_local, name, default) if _local is not None else default


# The one staff member whose property line-items are hidden dashboard-wide, and
# the words that mark an item as a property rather than a tax statement
# ("16 Franklin Street", "104 Winding Way", "719 Tenant", ...). Blank disables it.
HIDDEN_ITEM_ASSIGNEE = _firm_setting("HIDDEN_ITEM_ASSIGNEE", "")
HIDDEN_ITEM_WORDS = tuple(_firm_setting("HIDDEN_ITEM_WORDS", ()))

# Internal/test clients that should never appear anywhere on the dashboard, PDF
# or Excel exports. Written as they read in Karbon; matching normalises both
# sides (see _normalize_client) so case, spacing and punctuation don't matter.
EXCLUDED_CLIENT_NAMES = set(_firm_setting("EXCLUDED_CLIENT_NAMES", ()))

# Former/inactive staff whose few remaining documents should still count
# everywhere (Overview, Overdue, Returns, Weekly Review, staff dropdowns), just
# relabelled "Unassigned" rather than shown under their own name. Preferred over
# deleting or reassigning their documents.
UNASSIGNED_STAFF_NAMES = {str(n).strip().lower()
                          for n in _firm_setting("UNASSIGNED_STAFF_NAMES", ())}

# The firm's display name, used as the heading of the weekly review (and anywhere
# else the firm needs naming).
FIRM_NAME = _firm_setting("FIRM_NAME", "NLC Financial")

# The Weekly Review covers only clients whose Client Owner starts with this
# (lower-cased) prefix, plus clients with no owner recorded at all. Blank — the
# default — means no owner filtering: every client is in scope. See
# data/review.py::_owner_in_scope.
REVIEW_OWNER_PREFIX = str(_firm_setting("REVIEW_OWNER_PREFIX", "")).strip().lower()


@functools.lru_cache(maxsize=8)
def _hidden_words_re(words: tuple):
    """Whole-word, case-insensitive matcher for `words`, or None if empty.

    Cached on the words tuple rather than built once at import, so a test (or a
    reloaded local_config) that changes HIDDEN_ITEM_WORDS is picked up. The
    empty case MUST return None, not a compiled pattern: `\\b(?:)\\b` matches
    everywhere and would hide the entire dashboard.
    """
    if not words:
        return None
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b",
                      re.IGNORECASE)


def is_hidden_item(assignee, client, title) -> bool:
    """True if this item should be hidden dashboard-wide: it belongs to
    HIDDEN_ITEM_ASSIGNEE and its client/title names a property (see above).
    Only that one assignee is affected — the same words on anyone else's work
    are left alone."""
    if not HIDDEN_ITEM_ASSIGNEE:
        return False
    if (assignee or "").strip().lower() != HIDDEN_ITEM_ASSIGNEE.strip().lower():
        return False
    pattern = _hidden_words_re(HIDDEN_ITEM_WORDS)
    return bool(pattern and pattern.search(f"{client or ''} {title or ''}"))


_CLIENT_PUNCT_RE = re.compile(r"[,.]")
_CLIENT_WS_RE = re.compile(r"\s+")


def _normalize_client(client) -> str:
    """A client name reduced to a comparison key: lowercased, commas and periods
    dropped, runs of whitespace collapsed.

    Karbon exports the same entity with inconsistent punctuation -- a name has
    arrived both with and without its comma -- and an exact-string match let 121
    internal documents back into the counts on that one character. Only
    punctuation and spacing are normalized: the words themselves must still
    match, so distinct clients that merely share a prefix are unaffected.
    """
    s = _CLIENT_PUNCT_RE.sub("", (client or "").lower())
    return _CLIENT_WS_RE.sub(" ", s).strip()


@functools.lru_cache(maxsize=8)
def _excluded_client_keys(names: frozenset) -> frozenset:
    return frozenset(_normalize_client(n) for n in names)


def is_excluded_client(client) -> bool:
    """True if this client should be hidden dashboard-wide (see EXCLUDED_CLIENT_NAMES)."""
    key = _normalize_client(client)
    return bool(key) and key in _excluded_client_keys(frozenset(EXCLUDED_CLIENT_NAMES))


def normalize_assignee(assignee: str) -> str:
    """The assignee value every view should see: "Unassigned" in place of a
    name in UNASSIGNED_STAFF_NAMES, otherwise `assignee` unchanged. Applied
    once at the single source every view reads from (service.dashboard_data)."""
    if (assignee or "").strip().lower() in UNASSIGNED_STAFF_NAMES:
        return "Unassigned"
    return assignee

# Logical fields the dashboard understands. The user maps their own CSV column
# names onto these on the import screen. `required` fields must be mapped before
# an import can proceed; the others are optional but improve the analytics.
LOGICAL_FIELDS = [
    ("assignee", "Assignee / staff member", True),
    ("client", "Client", True),
    ("client_owner", "Client owner / partner", False),
    ("title", "Document / work title", True),
    ("status", "Status", True),
    ("date", "Start / created / opened date", False),
    ("due_date", "Due date", False),
    # Karbon's per-document completion timestamp ("Completed Date UTC"). The one
    # trustworthy source for WHEN an individual return finished — the Weekly
    # Review's "Completed this week" counts these documents, not whole clients.
    ("completed_date", "Completed date (per document)", False),
    ("item_id", "Karbon work ID (stable key)", False),
    ("project", "Return / project (groups documents)", False),
    ("return_type", "Return type (1040, 1120, ...)", False),
]
REQUIRED_FIELDS = [name for name, _label, req in LOGICAL_FIELDS if req]

# The label used when a document/return has no type in the data.
UNCLASSIFIED = "Unclassified"

# Legacy: the old fixed Individual/Business buckets. Kept only because the
# legacy desktop UI (src/ui/projects_view.py) still imports it. The web app no
# longer buckets — it classifies by the specific type from the data (below).
RETURN_TYPES = ["Individual", "Business", "Unclassified"]


def normalize_return_type(raw: str | None) -> str:
    """The document/return type, kept as specific as the data allows.

    We use the exact value from the mapped "return type" column, just trimmed —
    e.g. "Tax: 1040", "Accounting/Bookkeeping", "Tax: 1120S (S-Corp)". Brand-new
    types appear automatically the moment they show up in an import; nothing has
    to be pre-registered. Blank -> "Unclassified".
    """
    s = ("" if raw is None else str(raw)).strip()
    return s or UNCLASSIFIED

# Default overdue threshold in days (the user can change this live in Settings).
DEFAULT_OVERDUE_DAYS = 14

# Buckets used for the "age distribution" chart, in days. Each entry is the
# inclusive upper bound of the bucket; the final bucket catches everything above.
AGE_BUCKETS = [3, 7, 14, 30, 60]


def default_data_dir() -> Path:
    """Per-user folder for the database and settings.

    Uses %LOCALAPPDATA% on Windows so the app works from a read-only network
    drive (the .exe can live anywhere; its data is written to the user profile).
    """
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = Path(base) / "KarbonPendingDashboard"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_db_path() -> Path:
    return default_data_dir() / "dashboard.db"


def default_import_upload_dir() -> Path:
    """Where an uploaded import file waits between "Upload" and "Run import".

    Deliberately NOT the OS temp dir: on this deployment (Remote Desktop
    Services), `tempfile.gettempdir()` resolves to a numbered per-session
    folder that can become invalid mid-wizard (e.g. if the app process
    restarts) -- silently vanishing the file the user just uploaded. This
    folder lives under the same stable per-user app-data directory as the
    database, so it survives process restarts.
    """
    d = default_data_dir() / "import_uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_log_path() -> Path:
    return default_data_dir() / "app.log"


def _app_config_path() -> Path:
    return default_data_dir() / "app_config.json"


def read_app_config() -> dict:
    """Small JSON config that lives outside the database (e.g. which DB to open)."""
    p = _app_config_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def write_app_config(cfg: dict) -> None:
    try:
        _app_config_path().write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except OSError:
        pass


def active_db_path() -> Path:
    """The DB to open on startup: the user's chosen path, else the default."""
    cfg = read_app_config()
    chosen = cfg.get("db_path")
    return Path(chosen) if chosen else default_db_path()


def get_or_create_secret_key() -> str:
    """Return a stable random key for signing Flask session cookies.

    Persisted per-machine in the data dir (deliberately separate from the
    possibly-shared database) so sessions survive restarts and can't be forged
    the way the old hardcoded key allowed. Each machine signs its own cookies;
    accounts themselves are shared via the database. Falls back to an ephemeral
    key if the file can't be read or written.
    """
    key_path = default_data_dir() / "secret_key"
    try:
        if key_path.exists():
            existing = key_path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
    except OSError:
        pass

    key = secrets.token_hex(32)
    try:
        key_path.write_text(key, encoding="utf-8")
        try:
            os.chmod(key_path, 0o600)  # best-effort; ignored on some filesystems
        except OSError:
            pass
    except OSError:
        pass  # ephemeral key: sessions won't survive restart, but app still runs
    return key
