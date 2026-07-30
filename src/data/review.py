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
# The review covers clients owned by Owen PLUS clients with no Client Owner
# recorded at all (a blank export field, e.g. never set in Karbon) — but never a
# different, named owner (e.g. "Marcus Lorne"). A blank owner is treated as "not
# excluded" rather than silently dropped, so work doesn't fall out of the review
# just because Karbon's Client Owner column was left empty. The review never
# shows the "NO CORRESPONDENCE" statuses either. Owner is matched by prefix (the
# data has "Owen Bradfield"); the status exclusion matches the SUBSTRING so it
# catches "Ready To Start - NO CORRESPONDENCE" without touching legitimate
# correspondence work titles (e.g. "IRS Correspondence - Refund Issue"), which
# live in the title, not the status.
OWNER_PREFIX = "Owen"
EXCLUDE_STATUS_SUBSTR = "no correspondence"

# Bogus / non-human assignees that must never appear in the review — e.g. Karbon's
# own "Karbon Support" system account, which is not a real staff member. Matched on
# the normalized name, lower-cased; add here if more system accounts turn up.
EXCLUDE_ASSIGNEES = {"karbon support"}


def _is_excluded_assignee(name) -> bool:
    return _norm_assignee(name).lower() in EXCLUDE_ASSIGNEES


def _owner_in_scope(owner) -> bool:
    o = (owner or "").strip().lower()
    return not o or o.startswith(OWNER_PREFIX)


def _in_scope(it: dict) -> bool:
    status = (it.get("status") or "").lower()
    return (_owner_in_scope(it.get("client_owner"))
            and EXCLUDE_STATUS_SUBSTR not in status
            and not _is_excluded_assignee(it.get("assignee")))


def _week_start_sunday(d: date) -> date:
    """The most recent Sunday 00:00 on or before `d`. Python weekday(): Mon=0..Sun=6,
    so days since Sunday is (weekday + 1) % 7."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


# How many days the "Completed this week" window spans, counting the import day
# itself — i.e. the import date and the six days before it.
COMPLETED_WINDOW_DAYS = 7


def _completed_window(anchor: date) -> tuple[date, date]:
    """The trailing 7-day completion window ending on `anchor` (the import date).

    Deliberately NOT pinned to a calendar weekday: the review answers "what
    finished in the week leading up to this import", so importing on a Wednesday
    covers the previous Thursday through that Wednesday. Anchoring to the import
    date (rather than a fixed Sunday/Monday) means nothing completed just before
    a calendar boundary silently falls out of every week's report.
    """
    return anchor - timedelta(days=COMPLETED_WINDOW_DAYS - 1), anchor


def _completed_date(it: dict) -> date | None:
    """A document's own completion date from Karbon's "Completed Date UTC"
    column, or None when the export didn't provide one."""
    cd = it.get("completed_date")
    if cd is None or cd == "":
        return None
    if isinstance(cd, date):
        return cd
    try:
        return date.fromisoformat(str(cd)[:10])
    except (ValueError, TypeError):
        return None


def _completed_in_window(it: dict, start: date, end: date) -> bool:
    """True when this DOCUMENT genuinely completed inside the window.

    Counts individual documents (one return each), never whole clients — the
    project-level tally this replaced grouped by client (analytics.build_projects
    falls back to a 'c:'+client key when the export has no project column), which
    inflated one finished client into one "completion" no matter how many returns
    it held, and vice versa.

    "Completed - Cancelled" / "- Not a fit" / "- Billed" are closed out but are
    not real completions (see service.dashboard_data), so they're excluded here
    too — matching what the Completed tab counts.
    """
    cd = _completed_date(it)
    if cd is None or not (start <= cd <= end):
        return False
    if it.get("closed_other"):
        return False
    return _in_scope(it)


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


def _doc_type(it: dict) -> str:
    """This DOCUMENT's own work type, straight from the export's Work Type column.

    Deliberately not `_item_type`: service.dashboard_data overwrites every item's
    `return_type` with its PROJECT's effective type, and projects group
    client-level when the export has no project column — so a client's
    "2025 Year-End Review" would render under whatever type won for that client
    overall (e.g. "Payroll"). A per-document list has to show the document's own
    type or the column is actively misleading.
    """
    return normalize_return_type(it.get("return_type_raw")) or _item_type(it)


def _opened_iso(it: dict) -> str:
    """When a statement 'opened' — the CSV date if present, else when first seen —
    as an ISO date string ('' if neither), mirroring analytics.item_age_days."""
    op = it.get("source_date") or it.get("first_seen")
    if hasattr(op, "isoformat"):
        return op.isoformat()
    return str(op)[:10] if op else ""


def _stmt_row(it: dict, rank: int) -> dict:
    """One row for a statements table (firm Top-N, per-staff Top-10, recent-10)."""
    return {
        "rank": rank,
        "client": it.get("client", ""),
        "title": it.get("title", ""),
        "return_type": _item_type(it),
        "days_overdue": it.get("days_overdue", 0),
        "days_open": it.get("age_days", 0),
        "assignee": _norm_assignee(it.get("assignee")),
        "opened": _opened_iso(it),
    }


def _project_title_label(p: dict) -> str:
    """A return can bundle several documents (grouping is client-level, see
    analytics.build_projects) with different titles, e.g. "1040 Return" +
    "State Return" -- join them the same way assignee_label joins staff names.

    build_projects precomputes this as `title_label`; recompute it only for
    callers (tests) that hand us a bare project dict without one."""
    label = p.get("title_label")
    if label:
        return label
    titles = sorted({d["title"].strip() for d in (p.get("documents") or [])
                     if d.get("title", "").strip()})
    return (", ".join(titles) if len(titles) <= 2
            else f"{titles[0]} +{len(titles) - 1}")


def _types_list(counter) -> list:
    """A Counter of {type: n} as [{type, count}, ...], most common first, ties
    broken alphabetically so the order is stable across renders."""
    return [{"type": t, "count": c}
            for t, c in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0].lower()))]


def _staff_project_stats(data: dict, week_start: date, week_end: date) -> dict:
    """Per-staff tallies over the Owen-owned work, at (client, work type) grain —
    e.g. "Acme Corp / Payroll" and "Acme Corp / Tax: 1040" are tallied as two
    separate engagements, each open/overdue only if ITS OWN documents need work.

    Deliberately NOT `data["projects"]` (analytics.build_projects): a Karbon
    export usually has no per-return "Project" tag, so build_projects falls back
    to grouping EVERY document for a client into one bucket regardless of work
    type — one lingering open Tax Return then keeps an otherwise-finished Payroll
    engagement reading as "open" for the whole client, and the client's mixed bag
    of types collapses into whichever type happened to come first. The Weekly
    Review answers "is this work type done for this client", so it re-groups
    `data["items"]` itself here, using each document's own type (`_doc_type`, the
    export's raw Work Type column) rather than the merged project-level type.
    NOTE: a manual project-type override set via Returns & Bookkeeping (for
    clients whose export type is blank) is a whole-client override tied to the
    old client-level grouping, so it does NOT carry over to this per-type view —
    such a client's undated documents still show as "Unclassified" here.

    Returns name -> {overdue, open, completed_week, overdue_by_type: Counter,
    open_by_type: Counter}. `overdue`/`open` count ENGAGEMENTS (client + work
    type pairs), not individual documents. `completed_week` is DOCUMENT-level
    over the trailing 7-day window, so it matches the headline "Completed this
    week" list exactly.
    """
    stats: dict[str, dict] = {}

    def bucket(name: str) -> dict:
        return stats.setdefault(name, {
            "overdue": 0, "open": 0, "completed_week": 0,
            "overdue_by_type": Counter(), "open_by_type": Counter(),
        })

    engagements: dict[tuple, dict] = {}
    for it in data.get("items", []):
        if not _owner_in_scope(it.get("client_owner")):
            continue
        key = (it.get("client", ""), _doc_type(it))
        e = engagements.setdefault(key, {"open": False, "overdue": False, "names": set()})
        if not _is_excluded_assignee(it.get("assignee")):
            e["names"].add(_norm_assignee(it.get("assignee")))
        if not it.get("closed"):
            e["open"] = True
            if it.get("overdue"):
                e["overdue"] = True

    for (client, rtype), e in engagements.items():
        if not e["names"]:      # worked only by an excluded system account — drop it
            continue
        for name in e["names"]:
            b = bucket(name)
            if e["overdue"]:
                b["overdue"] += 1
                b["overdue_by_type"][rtype] += 1
            if e["open"]:
                b["open"] += 1
                b["open_by_type"][rtype] += 1

    # Completions are counted per DOCUMENT and credited to that document's own
    # assignee — not spread across everyone who touched the return — so summing
    # every staff member's completed_week reproduces the headline total.
    for it in data.get("items", []):
        if not _completed_in_window(it, week_start, week_end):
            continue
        bucket(_norm_assignee(it.get("assignee")))["completed_week"] += 1
    return stats


def build_review(data: dict, firm_name: str, generated_at, top_n: int = 10,
                 per_staff_limit: int = 10, last_import_date: date | None = None) -> dict:
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
    # "Completed this week" is the trailing 7 days ending on the most recent
    # IMPORT date -- see _completed_window. Counted per DOCUMENT from Karbon's
    # "Completed Date UTC", so the figure is the number of individual returns
    # that finished, not the number of clients that happened to close out.
    # `last_import_date` is the caller's source of truth (webapp.py passes
    # store.last_import()); fall back to `generated_at` when there's no import
    # history yet (e.g. a brand-new database).
    gen_date = generated_at.date() if hasattr(generated_at, "date") else generated_at
    week_start, week_end = _completed_window(last_import_date or gen_date)
    completed_week = []
    for it in data.get("items", []):
        if not _completed_in_window(it, week_start, week_end):
            continue
        completed_week.append({
            "client": it.get("client", ""),
            "title": it.get("title", ""),
            "return_type": _doc_type(it),
            "assignee": _norm_assignee(it.get("assignee")),
            "completed_at": _completed_date(it).isoformat(),
        })
    # Newest first, then client/title so same-day rows have a stable order.
    completed_week.sort(key=lambda r: (r["completed_at"], r["client"].lower(),
                                       r["title"].lower()), reverse=True)

    overdue = sorted((it for it in data.get("overdue", []) if _in_scope(it)),
                     key=lambda it: (it.get("days_overdue", 0), it.get("age_days", 0)),
                     reverse=True)

    top = [_stmt_row(it, i + 1) for i, it in enumerate(overdue[:top_n])]

    # Firm-wide "most recent" — the newest-OPENED in-scope statements, newest first
    # (blank open-dates sort last). Feeds the firm-wide page after the last staff
    # member; recomputed live each view, so it tracks the latest imported work.
    recent_items = sorted((it for it in data.get("items", []) if _in_scope(it)),
                          key=lambda it: (_opened_iso(it) or "", it.get("age_days", 0)),
                          reverse=True)
    recent = [_stmt_row(it, i + 1) for i, it in enumerate(recent_items[:top_n])]

    # Per staff (STATEMENT level): the full list of their overdue statements
    # (worst first) AND a count of those statements by return type. Kept for the
    # printable PDF (review_pdf.py), which still lays out the detailed tables.
    by_staff: dict[str, list] = {}
    for it in overdue:
        by_staff.setdefault(_norm_assignee(it.get("assignee")), []).append(it)

    per_staff = []
    for who, items in by_staff.items():
        counter = Counter(_item_type(it) for it in items)
        # Only the worst `per_staff_limit` are listed; count/types below cover all.
        statements = [_stmt_row(it, i + 1) for i, it in enumerate(items[:per_staff_limit])]
        per_staff.append({
            "assignee": who,
            "count": len(items),
            "statements": statements,
            "types": _types_list(counter),
        })
    # Busiest first, then name; Unassigned always last so it never leads.
    per_staff.sort(key=lambda s: (s["assignee"] == "Unassigned",
                                  -s["count"], s["assignee"].lower()))

    # Per staff (client + work-type engagement level): the three-point summary
    # the main page shows — staff name, their overdue-engagement total, and that
    # total broken out by type.
    # Each staff row links to their own detail page (see build_staff_page). A
    # staff member appears if they have ANY Owen-owned open/overdue/just-completed
    # work this week, so every relevant person gets a row and a page.
    pstats = _staff_project_stats(data, week_start, week_end)
    staff_rows = []
    for name, b in pstats.items():
        if not (b["overdue"] or b["open"] or b["completed_week"]):
            continue
        staff_rows.append({
            "assignee": name,
            "overdue": b["overdue"],
            "open": b["open"],
            "completed_week": b["completed_week"],
            "overdue_by_type": _types_list(b["overdue_by_type"]),
        })
    staff_rows.sort(key=lambda s: (s["assignee"] == "Unassigned",
                                   -s["overdue"], -s["open"], s["assignee"].lower()))

    return {
        "firm_name": firm_name,
        "generated_at": generated_at,
        "top_n": top_n,
        "week_start": week_start.isoformat(),
        "completed_this_week": completed_week,
        "top": top,
        "recent": recent,
        "per_staff": per_staff,
        "staff_rows": staff_rows,
        "total_overdue": len(overdue),
    }


def build_staff_page(data: dict, firm_name: str, generated_at, staff_name: str,
                     top_n: int = 10, recent_n: int = 10,
                     last_import_date: date | None = None) -> dict:
    """One staff member's detail page for the weekly review (Owen-owned scope).

    Headline tiles (completed this week / open / overdue, plus open broken out by
    work type) come from _staff_project_stats — the SAME tallies the main page's
    summary row uses, so a staff member's numbers match between the two. Open and
    overdue count (client, work type) engagements, so a client's finished Payroll
    work reads as done even while their Tax Return is still open; completed-this-
    week is document-level (see _staff_project_stats).
    `last_import_date` MUST be the same value passed to build_review for this
    review, or a staff member's "completed this week" count would disagree with the
    main page's list. Returns a dict ready for review_staff.html, with two detail
    lists: their Top-N most overdue statements (worst first, document-level) and
    their N most RECENTLY overdue PROJECTS (returns grouped client-level, like the
    Returns & Bookkeeping tab — freshest first, i.e. the ones that just crossed the
    overdue threshold, as a "newly at-risk" complement to the worst-first list).
    """
    gen_date = generated_at.date() if hasattr(generated_at, "date") else generated_at
    week_start, week_end = _completed_window(last_import_date or gen_date)
    who = _norm_assignee(staff_name)

    b = _staff_project_stats(data, week_start, week_end).get(who, {
        "overdue": 0, "open": 0, "completed_week": 0,
        "overdue_by_type": Counter(), "open_by_type": Counter(),
    })

    def _mine(it: dict) -> bool:
        return _in_scope(it) and _norm_assignee(it.get("assignee")) == who

    def _project_mine(p: dict) -> bool:
        if not _owner_in_scope(p.get("client_owner")):
            return False
        names = {_norm_assignee(a) for a in (p.get("assignees") or [])} or {"Unassigned"}
        names = {n for n in names if n.lower() not in EXCLUDE_ASSIGNEES}
        return who in names

    overdue = sorted((it for it in data.get("overdue", []) if _mine(it)),
                     key=lambda it: (it.get("days_overdue", 0), it.get("age_days", 0)),
                     reverse=True)
    top_overdue = [_stmt_row(it, i + 1) for i, it in enumerate(overdue[:top_n])]

    # Most recently overdue PROJECTS: smallest days_overdue first, i.e. the ones
    # that most recently crossed the overdue threshold -- a "just went overdue"
    # alert list, distinct from top_overdue's "longest-standing problem" ordering.
    recent_overdue_projects = sorted(
        (p for p in data.get("projects", []) if p.get("overdue") and _project_mine(p)),
        key=lambda p: (p["days_overdue"], p["client"].lower()))
    recent_overdue = [
        {"rank": i + 1, "client": p["client"], "title": _project_title_label(p),
         "return_type": p.get("return_type", ""), "days_overdue": p["days_overdue"]}
        for i, p in enumerate(recent_overdue_projects[:recent_n])
    ]

    return {
        "firm_name": firm_name,
        "generated_at": generated_at,
        "week_start": week_start.isoformat(),
        "staff": who,
        "found": bool(b["overdue"] or b["open"] or b["completed_week"] or recent_overdue_projects),
        "completed_week": b["completed_week"],
        "open": b["open"],
        "open_by_type": _types_list(b["open_by_type"]),
        "overdue": b["overdue"],
        "overdue_by_type": _types_list(b["overdue_by_type"]),
        "top_overdue": top_overdue,
        "recent_overdue": recent_overdue,
    }
