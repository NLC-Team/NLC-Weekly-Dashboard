"""The weekly review: a firm-wide "what needs finishing" snapshot.

Pure data in, pure data out — like analytics.py. It shapes the already-computed
dashboard data (from service.dashboard_data) into the two sections the review shows,
both at the **statement (document) level** so they line up with the dashboard's
"Overdue statements" view (overdue.html):

    1. Top N   — the individual overdue statements with the most DAYS OVERDUE,
                 firm-wide, ranked 1..N. Same list the Overdue tab ranks; a client
                 with several old documents can appear more than once.
    2. Per staff — each staff member's overdue statements COUNTED BY RETURN TYPE
                 (e.g. "5  Tax: 1040"), busiest staff first.

It consumes `data["overdue"]` — the exact overdue-item list the dashboard uses,
already with received documents and completed returns folded out — so the review
can't drift from what staff see on screen. No database, no Flask, no clock beyond
the `generated_at` passed in.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from config import normalize_return_type

# ---- Weekly-review scope (business rules, kept here so they're easy to change) --
# The review covers ONLY clients owned by Owen, and never the "NO CORRESPONDENCE"
# statuses. Owner is matched by prefix (the data has "Owen Bradfield"); the status
# exclusion matches the SUBSTRING so it catches "Ready To Start - NO CORRESPONDENCE"
# without touching legitimate correspondence work titles (e.g. "IRS Correspondence
# - Refund Issue"), which live in the title, not the status.
OWNER_PREFIX = "Owen"
EXCLUDE_STATUS_SUBSTR = "no correspondence"


def _in_scope(it: dict) -> bool:
    owner = (it.get("client_owner") or "").strip().lower()
    status = (it.get("status") or "").lower()
    return owner.startswith(OWNER_PREFIX) and EXCLUDE_STATUS_SUBSTR not in status


def _week_start_sunday(d: date) -> date:
    """The most recent Sunday 00:00 on or before `d`. Python weekday(): Mon=0..Sun=6,
    so days since Sunday is (weekday + 1) % 7."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _owner_in_scope(owner) -> bool:
    return (owner or "").strip().lower().startswith(OWNER_PREFIX)


def _norm_assignee(a) -> str:
    """Normalize a staff name the same way build_projects does: blank or the
    importer's literal "(unassigned)" both read as a single "Unassigned"."""
    who = (a or "").strip()
    if not who or who.lower() == "(unassigned)":
        return "Unassigned"
    return who


def _item_type(it: dict) -> str:
    """Effective return type for an item. service.dashboard_data sets `return_type`
    to the project's effective type; fall back to the raw CSV value for callers
    (tests) that hand us bare enriched items."""
    return it.get("return_type") or normalize_return_type(it.get("return_type_raw"))


def build_review(data: dict, firm_name: str, generated_at, top_n: int = 10,
                 per_staff_limit: int = 10) -> dict:
    """Shape dashboard_data into the weekly review. Returns:

        {
          firm_name, generated_at, top_n,
          top: [ {rank, client, title, return_type, days_overdue, days_open, assignee}, ... ],
          per_staff: [ {assignee, count, statements: [ {rank, client, title, ...}, ... up to
                        per_staff_limit ], types: [ {type, count}, ... ]}, ... ],
          total_overdue: int,   # overdue statements firm-wide (matches Overdue tab)
        }

    `statements` per staff is capped to their `per_staff_limit` worst; `count` and
    `types` still reflect ALL of that person's overdue statements, so the totals stay
    complete even though only the top few rows are listed.

    Ordering matches the dashboard: overdue statements by days_overdue, worst first.
    Scope: only Owen-owned clients, excluding NO CORRESPONDENCE statuses (see _in_scope).
    """
    # "Completed this week" — returns whose completion date is on/after the most
    # recent Sunday. Scoped like the rest of the review (Owen-owned). Reads the
    # project list (which already has the dashboard-wide hidden items removed).
    gen_date = generated_at.date() if hasattr(generated_at, "date") else generated_at
    week_start = _week_start_sunday(gen_date)
    completed_week = []
    for p in data.get("projects", []):
        if not (p.get("completed") and p.get("completed_at")):
            continue
        if not _owner_in_scope(p.get("client_owner")):
            continue
        try:
            cad = date.fromisoformat(str(p["completed_at"])[:10])
        except (ValueError, TypeError):
            continue
        if cad >= week_start:
            completed_week.append({
                "client": p.get("client", ""),
                "return_type": p.get("return_type", ""),
                "assignee": p.get("assignee_label", ""),
                "completed_at": cad.isoformat(),
            })
    completed_week.sort(key=lambda r: r["completed_at"], reverse=True)

    overdue = sorted((it for it in data.get("overdue", []) if _in_scope(it)),
                     key=lambda it: (it.get("days_overdue", 0), it.get("age_days", 0)),
                     reverse=True)

    def _row(it: dict, rank: int) -> dict:
        return {
            "rank": rank,
            "client": it.get("client", ""),
            "title": it.get("title", ""),
            "return_type": _item_type(it),
            "days_overdue": it.get("days_overdue", 0),
            "days_open": it.get("age_days", 0),
            "assignee": _norm_assignee(it.get("assignee")),
        }

    top = [_row(it, i + 1) for i, it in enumerate(overdue[:top_n])]

    # Per staff: the full list of their overdue statements (worst first) AND a
    # count of those statements by return type.
    by_staff: dict[str, list] = {}
    for it in overdue:
        by_staff.setdefault(_norm_assignee(it.get("assignee")), []).append(it)

    per_staff = []
    for who, items in by_staff.items():
        counter = Counter(_item_type(it) for it in items)
        # Only the worst `per_staff_limit` are listed; count/types below cover all.
        statements = [_row(it, i + 1) for i, it in enumerate(items[:per_staff_limit])]
        per_staff.append({
            "assignee": who,
            "count": len(items),
            "statements": statements,
            # Most common type first; ties broken alphabetically for a stable order.
            "types": [{"type": t, "count": c}
                      for t, c in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0].lower()))],
        })
    # Busiest first, then name; Unassigned always last so it never leads.
    per_staff.sort(key=lambda s: (s["assignee"] == "Unassigned",
                                  -s["count"], s["assignee"].lower()))

    return {
        "firm_name": firm_name,
        "generated_at": generated_at,
        "top_n": top_n,
        "week_start": week_start.isoformat(),
        "completed_this_week": completed_week,
        "top": top,
        "per_staff": per_staff,
        "total_overdue": len(overdue),
    }
