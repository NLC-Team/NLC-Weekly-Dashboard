"""CSV loading, column-mapping, and pending-status filtering.

The dashboard is deliberately schema-agnostic: a Karbon export can have any
column names. We load the raw CSV, let the user (or a heuristic) map the real
columns onto our logical fields, then turn each row into a normalised record.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date

import pandas as pd

from config import LOGICAL_FIELDS

# Keyword hints used to pre-guess the mapping so the user usually just confirms.
_GUESS_HINTS = {
    "assignee": ["assignee", "assigned", "owner", "staff", "preparer", "responsible", "team member", "user"],
    "client": ["client", "customer", "account", "entity", "contact"],
    "title": ["document", "form", "work title", "title", "task", "job", "name", "description", "subject"],
    "status": ["status", "state", "stage", "workflow"],
    "date": ["start date", "created", "due", "date", "deadline", "opened", "requested"],
    "item_id": ["work id", "document id", "id", "number", "ref", "reference"],
    "project": ["project", "engagement", "matter", "return id", "return name"],
    "return_type": ["return type", "work type", "engagement type", "form type", "type"],
}


def load_csv(path: str) -> pd.DataFrame:
    """Read a CSV as all-strings (so nothing is silently coerced)."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def header_signature(columns: list[str]) -> str:
    """Stable fingerprint of a CSV's columns, used to remember its mapping."""
    joined = "||".join(sorted(c.strip().lower() for c in columns))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def guess_mapping(columns: list[str]) -> dict:
    """Best-effort initial mapping of logical field -> source column name."""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    lowered = {c: c.lower() for c in columns}
    for field, _label, _req in LOGICAL_FIELDS:
        hints = _GUESS_HINTS.get(field, [])
        best = None
        # Prefer an exact-ish hint match; longer hints win (more specific).
        for hint in sorted(hints, key=len, reverse=True):
            for col in columns:
                if col in used:
                    continue
                if hint in lowered[col]:
                    best = col
                    break
            if best:
                break
        if best:
            mapping[field] = best
            used.add(best)
    return mapping


def _norm(value) -> str:
    return ("" if value is None else str(value)).strip()


def _make_key(rec: dict, raw_id: str) -> str:
    if raw_id:
        return f"id:{raw_id.strip().lower()}"
    basis = "|".join(_norm(rec[k]).lower() for k in ("client", "title", "assignee"))
    return "h:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def parse_date(value: str) -> date | None:
    value = _norm(value)
    if not value:
        return None
    ts = pd.to_datetime(value, errors="coerce", dayfirst=False)
    if pd.isna(ts):
        return None
    return ts.date()


def apply_mapping(df: pd.DataFrame, mapping: dict) -> list[dict]:
    """Turn raw rows into normalised records using the column mapping."""
    records = []
    for _, row in df.iterrows():

        def get(field):
            col = mapping.get(field)
            return _norm(row[col]) if col and col in df.columns else ""

        rec = {
            "assignee": get("assignee") or "(unassigned)",
            "client": get("client"),
            "title": get("title"),
            "status": get("status"),
            "source_date": parse_date(get("date")),
            "return_type_raw": get("return_type"),
        }
        # Documents group into a project: an explicit project/return column if
        # mapped, otherwise one project per client.
        proj = get("project")
        if proj:
            rec["project_key"] = "p:" + proj.strip().lower()
        else:
            rec["project_key"] = "c:" + rec["client"].strip().lower()
        rec["item_key"] = _make_key(rec, get("item_id"))
        records.append(rec)
    return records


def distinct_statuses(records: list[dict]) -> list[str]:
    """Sorted unique status values present in the data."""
    return sorted({r["status"] for r in records if r["status"]})


def filter_pending(records: list[dict], pending_statuses: list[str] | None) -> list[dict]:
    """Keep only records whose status counts as 'pending to send'.

    Matching is case-insensitive. An empty/None selection means 'treat every row
    as pending' (sensible default before the user has configured statuses).
    """
    if not pending_statuses:
        return list(records)
    wanted = {s.strip().lower() for s in pending_statuses}
    return [r for r in records if r["status"].strip().lower() in wanted]
