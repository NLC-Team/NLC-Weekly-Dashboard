"""Application configuration: paths, logical fields, and default settings.

Everything that the rest of the app needs to know about *where* data lives and
*what* the sensible defaults are is centralised here so it can be changed in one
place (and so the packaged .exe behaves predictably).
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

APP_NAME = "Karbon Pending Dashboard"

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
    ("title", "Document / work title", True),
    ("status", "Status", True),
    ("date", "Start / created / due date", False),
    ("item_id", "Karbon work ID (stable key)", False),
    ("project", "Return / project (groups documents)", False),
    ("return_type", "Return type (1040, 1120, ...)", False),
]
REQUIRED_FIELDS = [name for name, _label, req in LOGICAL_FIELDS if req]

# Keyword rules to auto-classify a return as Individual vs Business from a
# "return type" column. Anything unmatched stays Unclassified until the user
# sets it manually. (Order: check Individual first.)
RETURN_TYPES = ["Individual", "Business", "Unclassified"]
_RETURN_TYPE_HINTS = {
    "Individual": ["1040", "individual", "personal", "schedule c only"],
    "Business": ["1120", "1065", "1120s", "1120-s", "business", "corp", "corporation",
                 "partnership", "s-corp", "scorp", "llc", "company"],
}


def classify_return_type(raw: str | None) -> str:
    """Best-effort Individual/Business classification from a raw type string."""
    if not raw:
        return "Unclassified"
    low = str(raw).lower()
    for rtype in ("Individual", "Business"):
        if any(hint in low for hint in _RETURN_TYPE_HINTS[rtype]):
            return rtype
    return "Unclassified"

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
