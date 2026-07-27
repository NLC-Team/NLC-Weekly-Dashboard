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
    "assignee": ["assignee", "assigned", "staff", "preparer", "responsible", "team member", "user"],
    "client": ["client", "customer", "account", "entity", "contact"],
    # The relationship owner / partner, distinct from the per-document assignee.
    # Specific multi-word hints first so this doesn't grab the assignee column.
    "client_owner": ["client owner", "client manager", "client partner",
                     "engagement partner", "relationship manager", "relationship partner",
                     "account manager", "partner", "manager", "owner"],
    "title": ["document", "form", "work title", "title", "task", "job", "name", "description", "subject"],
    "status": ["status", "state", "stage", "workflow"],
    "date": ["start date", "created", "opened", "requested", "date"],
    "due_date": ["due date", "due", "deadline", "target date"],
    "item_id": ["work id", "document id", "id", "number", "ref", "reference"],
    "project": ["project", "engagement", "matter", "return id", "return name"],
    "return_type": ["return type", "work type", "engagement type", "form type", "type"],
}


def load_file(path: str) -> pd.DataFrame:
    """Read a CSV or Excel file as all-strings (so nothing is silently coerced)."""
    p = path.lower()
    if p.endswith(".xlsx") or p.endswith(".xls") or p.endswith(".xlsm"):
        df = pd.read_excel(path, dtype=str, keep_default_na=False)
    else:
        try:
            df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        except UnicodeDecodeError:
            df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="latin-1")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def load_csv(path: str) -> pd.DataFrame:
    """Backwards-compatible alias."""
    return load_file(path)


def header_signature(columns: list[str]) -> str:
    """Stable fingerprint of a CSV's columns, used to remember its mapping."""
    joined = "||".join(sorted(c.strip().lower() for c in columns))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def guess_mapping(columns: list[str]) -> dict:
    """Best-effort initial mapping of logical field -> source column name."""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    lowered = {c: c.lower() for c in columns}
    # Resolve "due_date" before the generic "date" (start date) field: "date" is
    # a deliberately loose fallback hint (it also has to catch a column literally
    # named "Date") which would otherwise match "Due Date"/"Deadline" columns
    # first and steal them before due_date's own hints get a turn. Stable sort
    # keeps every other field's relative LOGICAL_FIELDS order unchanged.
    fields = sorted(LOGICAL_FIELDS, key=lambda f: f[0] == "date")
    for field, _label, _req in fields:
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
    """Turn raw rows into normalised records using the column mapping.

    Optimised for large files: each mapped column is pulled once as a Python list
    and the date column is parsed in a single vectorised pass (per-row
    pd.to_datetime is ~100x slower). Duplicate item_keys within one file — common
    when there's no unique-ID column and two rows share client+title+assignee —
    are disambiguated with a #N suffix so NO rows are lost and a batch insert can't
    hit a UNIQUE collision. The first occurrence keeps the bare key, so previously
    imported single rows keep the same key.
    """
    n = len(df)

    def col_list(field):
        c = mapping.get(field)
        if c and c in df.columns:
            return [_norm(v) for v in df[c].tolist()]
        return [""] * n

    assignee = col_list("assignee")
    client = col_list("client")
    client_owner = col_list("client_owner")
    title = col_list("title")
    status = col_list("status")
    return_type = col_list("return_type")
    project = col_list("project")
    item_id = col_list("item_id")

    # Vectorised date parse: one pass over the whole column, not one call per cell.
    def parse_date_col(field):
        col = mapping.get(field)
        if col and col in df.columns:
            ds = pd.to_datetime(df[col], errors="coerce", dayfirst=False)
            return [None if pd.isna(t) else t.date() for t in ds]
        return [None] * n

    source_dates = parse_date_col("date")
    due_dates = parse_date_col("due_date")

    records = []
    seen: dict[str, int] = {}
    for i in range(n):
        rec = {
            "assignee": assignee[i] or "(unassigned)",
            "client": client[i],
            "client_owner": client_owner[i],
            "title": title[i],
            "status": status[i],
            "source_date": source_dates[i],
            "due_date": due_dates[i],
            "return_type_raw": return_type[i],
        }
        rec["project_key"] = ("p:" + project[i].strip().lower() if project[i]
                              else "c:" + client[i].strip().lower())
        base = _make_key(rec, item_id[i])
        cnt = seen.get(base, 0)
        seen[base] = cnt + 1
        rec["item_key"] = base if cnt == 0 else f"{base}#{cnt + 1}"
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
