"""Application configuration: paths, logical fields, and default settings.

Everything that the rest of the app needs to know about *where* data lives and
*what* the sensible defaults are is centralised here so it can be changed in one
place (and so the packaged .exe behaves predictably).
"""
from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path

APP_NAME = "Karbon Pending Dashboard"

# --- Hidden property line-items -------------------------------------------
# One staff member (Property Assignee) tracks real-estate/property line
# items whose client or title names a property ("16 Franklin Street", "104
# Winding Way", "719 Tenant", ...). These are not tax statements and the firm
# wants them hidden from the WHOLE dashboard — but ONLY under that assignee, so
# the same words on anyone else's work are untouched. Matched as whole words,
# case-insensitive, against the client name and the statement title.
HIDDEN_ITEM_ASSIGNEE = "Property Assignee"
_HIDDEN_ITEM_WORDS = ("street", "deerfield", "way", "place", "tenant")
_HIDDEN_ITEM_RE = re.compile(r"\b(?:" + "|".join(_HIDDEN_ITEM_WORDS) + r")\b", re.IGNORECASE)


def is_hidden_item(assignee, client, title) -> bool:
    """True if this item should be hidden dashboard-wide: it belongs to
    HIDDEN_ITEM_ASSIGNEE and its client/title names a property (see above)."""
    if (assignee or "").strip().lower() != HIDDEN_ITEM_ASSIGNEE.lower():
        return False
    return bool(_HIDDEN_ITEM_RE.search(f"{client or ''} {title or ''}"))

# Former/inactive staff whose few remaining documents should still count
# everywhere on the dashboard (Overview, Overdue, Returns, Weekly Review, staff
# dropdowns), just relabeled "Unassigned" rather than shown under their own
# name. Matched case-insensitively, trimmed. Add a name here rather than
# deleting/reassigning their documents.
UNASSIGNED_STAFF_NAMES = {"clara bexley", "noor rahimi", "ivy fenwick"}


def normalize_assignee(assignee: str) -> str:
    """The assignee value every view should see: "Unassigned" in place of a
    name in UNASSIGNED_STAFF_NAMES, otherwise `assignee` unchanged. Applied
    once at the single source every view reads from (service.dashboard_data)."""
    if (assignee or "").strip().lower() in UNASSIGNED_STAFF_NAMES:
        return "Unassigned"
    return assignee

# The firm's display name, used as the heading of the weekly review (and anywhere
# else the firm needs naming). Kept here so it changes in one place.
FIRM_NAME = "NLC Financial"

# Only email addresses on this domain may sign up / hold an account. Change this
# one value if the firm's domain ever changes.
ALLOWED_EMAIL_DOMAIN = "nlcfcpa.com"


def is_allowed_email(email: str) -> bool:
    """True if `email` is a plausibly-formed address on the allowed domain."""
    if not isinstance(email, str):
        return False
    e = email.strip().lower()
    return (
        e.count("@") == 1
        and " " not in e
        and "." in e.split("@", 1)[1]
        and e.endswith("@" + ALLOWED_EMAIL_DOMAIN)
    )

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
