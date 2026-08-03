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
from datetime import date, datetime, timedelta

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


def _types_list(counter) -> list:
    """A Counter of {type: n} as [{type, count}, ...], most common first, ties
    broken alphabetically so the order is stable across renders."""
    return [{"type": t, "count": c}
            for t, c in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0].lower()))]


def _staff_doc_stats(data: dict, week_start: date, week_end: date) -> dict:
    """Per-staff tallies over the Owen-owned work, at DOCUMENT grain — same
    scope and "open" definition as build_staff_workbook (the Excel export), so
    a staff member's OPEN count and document lists here always agree with their
    own Excel worklist. Two narrow exceptions, both confirmed on real data:
      (a) OVERDUE count on a due-today document: this counts overdue via
          `it["overdue"]`, but build_staff_workbook's overdue_count uses
          `days_overdue > 0` — a document due exactly today has overdue=True
          and days_overdue=0 (see analytics.enrich_items), so on that one
          day the tile can show one more overdue than the Excel sheet. Open
          counts and the document lists themselves always agree regardless.
      (b) WHO APPEARS AT ALL: a staff member with completions this week but no
          open work gets a screen row, detail page and PDF page here
          (build_review keeps anyone with completed_week > 0), but no Excel
          sheet at all (build_staff_workbook keys only on open documents).

    Returns name -> {overdue, open, completed_week, overdue_by_type: Counter,
    open_by_type: Counter}. `overdue`/`open` count DOCUMENTS currently assigned
    to that person, not (client, work type) engagements — a person whose own
    document in an engagement is closed is NOT credited just because a
    different assignee's document in that same engagement is still open.
    `completed_week` is document-level over the trailing 7-day window, so it
    matches the headline "Completed this week" list exactly.
    """
    stats: dict[str, dict] = {}

    def bucket(name: str) -> dict:
        return stats.setdefault(name, {
            "overdue": 0, "open": 0, "completed_week": 0,
            "overdue_by_type": Counter(), "open_by_type": Counter(),
        })

    for it in data.get("items", []):
        if it.get("closed") or not _in_scope(it):
            continue
        rtype = _doc_type(it)
        b = bucket(_norm_assignee(it.get("assignee")))
        b["open"] += 1
        b["open_by_type"][rtype] += 1
        if it.get("overdue"):
            b["overdue"] += 1
            b["overdue_by_type"][rtype] += 1

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

    # Per staff (DOCUMENT level): the three-point summary the main page shows —
    # staff name, their open/overdue-document totals, and the overdue total
    # broken out by type. Counts only documents CURRENTLY assigned to that
    # person and still open/overdue — the same scope and definition
    # build_staff_workbook (the Excel) uses, so a staff member's numbers here
    # match their own Excel sheet with the two exceptions noted on
    # _staff_doc_stats (a due-today overdue count; someone who only completed
    # work this week gets no Excel sheet at all).
    # Each staff row links to their own detail page (see build_staff_page). A
    # staff member appears if they have ANY Owen-owned open/overdue/just-completed
    # work this week, so every relevant person gets a row and a page.
    pstats = _staff_doc_stats(data, week_start, week_end)
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


def _as_date(v) -> date | None:
    """A stored date column as a real `date`, or None when blank/unparsable.
    store.active_items already hands us `date` objects; tests and older rows can
    still carry ISO strings, so accept both."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


def _start_date(it: dict) -> date | None:
    """When the work started: the export's Start Date, else when we first saw the
    row — the same fallback analytics.item_age_days and _opened_iso use, so the
    Start column never disagrees with Days open."""
    return _as_date(it.get("source_date")) or _as_date(it.get("first_seen"))


def _xlsx_row(it: dict) -> dict:
    """One spreadsheet row for the per-staff Excel export, at DOCUMENT grain.

    Deliberately not _stmt_row: that one is built for the ranked report tables
    (it carries `rank`/`opened` and has no status or due date). Dates stay as real
    `date` objects so the writer can hand Excel real dates rather than text that
    only looks like a date.

    `work_type` is a LITERAL copy of the export's own Work Type cell for this
    document (_doc_type -> normalize_return_type just trims it; blank reads
    "Unclassified"). There is deliberately NO project-level type column: with no
    Project column in a Karbon export, build_projects groups by CLIENT and gives
    the whole client one type taken from its first non-blank document, so on real
    data that value contradicts the document's own Work Type on ~64% of rows —
    including clients whose collapsed type has no open work at all.
    """
    return {
        "client": it.get("client", ""),
        "title": it.get("title", ""),
        "status": (it.get("status") or "").strip(),
        "work_type": _doc_type(it),
        "start_date": _start_date(it),
        "due_date": _as_date(it.get("due_date")),
        "days_overdue": it.get("days_overdue", 0),
        "days_open": it.get("age_days", 0),
        "assignee": _norm_assignee(it.get("assignee")),
    }


def build_staff_workbook(data: dict, firm_name: str, generated_at) -> dict:
    """The weekly review as one worklist PER STAFF MEMBER, ready for Excel.

    Same live data and the same scope as the rest of the review (_in_scope: Owen-
    owned or blank-owner clients, no NO CORRESPONDENCE statuses, no Karbon Support),
    but shaped as a worklist instead of a report:

      * one row per DOCUMENT — nothing merged, so Title/Status/Start/Due are the
        document's own values (a client with a 1040 Return and a State Return under
        Tax: 1040 gets two rows);
      * only OPEN work — `closed` covers both real completions and the closed-out
        "Completed - Cancelled / - Not a fit / - Billed" statuses, and nothing is
        due on either, so neither belongs on a worklist;
      * grouped into work-type blocks, WORST OVERDUE FIRST inside each block and
        the block holding the worst item first, so the top of every sheet is the
        most urgent thing that person owns.

    Grouping is by each document's OWN Work Type (_doc_type), not the project-merged
    type — a Karbon export usually has no per-return Project tag, so build_projects
    falls back to grouping every document for a client into one bucket regardless of
    work type, which misleads at row level; every per-document view in this module
    uses the document's own type instead.
    Rows that aren't overdue yet still appear, with days_overdue 0, below the
    overdue ones.

    Returns:
        {firm_name, generated_at,
         sheets: [{assignee, open_count, overdue_count,
                   groups: [{work_type, max_days_overdue, rows: [_xlsx_row, ...]}]}]}

    Sheets are ordered most-overdue staff first (Unassigned always last), matching
    the on-screen staff summary. Purely data — the openpyxl writing lives in
    review_xlsx.py.
    """
    by_staff: dict[str, list] = {}
    for it in data.get("items", []):
        if it.get("closed") or not _in_scope(it):
            continue
        row = _xlsx_row(it)
        by_staff.setdefault(row["assignee"], []).append(row)

    sheets = []
    for who, rows in by_staff.items():
        groups: dict[str, list] = {}
        for r in rows:
            groups.setdefault(r["work_type"], []).append(r)
        blocks = []
        for wtype, grows in groups.items():
            grows.sort(key=lambda r: (-r["days_overdue"], -r["days_open"],
                                      r["client"].lower(), r["title"].lower()))
            blocks.append({"work_type": wtype,
                           "max_days_overdue": max(r["days_overdue"] for r in grows),
                           "rows": grows})
        # The work type holding this person's worst-overdue document leads; then the
        # biggest pile; then alphabetical, so the order is stable across renders.
        blocks.sort(key=lambda b: (-b["max_days_overdue"], -len(b["rows"]),
                                   b["work_type"].lower()))
        sheets.append({
            "assignee": who,
            "open_count": len(rows),
            "overdue_count": sum(1 for r in rows if r["days_overdue"] > 0),
            "groups": blocks,
        })
    sheets.sort(key=lambda s: (s["assignee"] == "Unassigned", -s["overdue_count"],
                               -s["open_count"], s["assignee"].lower()))

    return {"firm_name": firm_name, "generated_at": generated_at, "sheets": sheets}


def build_staff_page(data: dict, firm_name: str, generated_at, staff_name: str,
                     top_n: int = 10, recent_n: int = 10,
                     last_import_date: date | None = None) -> dict:
    """One staff member's detail page for the weekly review (Owen-owned scope).

    Headline tiles (completed this week / open / overdue, plus open/overdue
    broken out by work type) come from _staff_doc_stats — the SAME tallies the
    main page's summary row uses, so a staff member's numbers match between the
    two, AND match their own Excel worklist (build_staff_workbook), with the
    two exceptions documented on _staff_doc_stats (a due-today overdue count;
    completions-only staff get no Excel sheet). open/overdue count DOCUMENTS
    currently assigned to this person, not (client, work type) engagements,
    so a person whose own piece of an engagement is done drops off entirely
    even if a colleague still owns the rest of it.
    `last_import_date` MUST be the same value passed to build_review for this
    review, or a staff member's "completed this week" count would disagree with the
    main page's list. Returns a dict ready for review_staff.html, with two detail
    lists built from the same document set: their Top-N most overdue statements
    (worst first) and their N most RECENTLY overdue statements (smallest
    days_overdue first, i.e. the ones that just crossed the overdue threshold, as
    a "newly at-risk" complement to the worst-first list).
    """
    gen_date = generated_at.date() if hasattr(generated_at, "date") else generated_at
    week_start, week_end = _completed_window(last_import_date or gen_date)
    who = _norm_assignee(staff_name)

    b = _staff_doc_stats(data, week_start, week_end).get(who, {
        "overdue": 0, "open": 0, "completed_week": 0,
        "overdue_by_type": Counter(), "open_by_type": Counter(),
    })

    def _mine(it: dict) -> bool:
        return _in_scope(it) and _norm_assignee(it.get("assignee")) == who

    def _doc_row(it: dict, rank: int) -> dict:
        # This person's own lists are per-DOCUMENT, so the type column must be the
        # document's own Work Type, not the project-merged type _stmt_row defaults to
        # (see _doc_type -- the merged value contradicts the document's own type on
        # most real rows).
        return {**_stmt_row(it, rank), "return_type": _doc_type(it)}

    my_overdue = [it for it in data.get("overdue", []) if _mine(it)]

    top_overdue = [
        _doc_row(it, i + 1)
        for i, it in enumerate(sorted(
            my_overdue,
            key=lambda it: (it.get("days_overdue", 0), it.get("age_days", 0)),
            reverse=True)[:top_n])
    ]

    # Freshest-overdue first (smallest days_overdue) -- the ones that most
    # recently crossed the overdue threshold, a "just went overdue" alert list,
    # distinct from top_overdue's "longest-standing problem" ordering. Same
    # document set as top_overdue, just sorted the other way. Tie-broken on
    # title too (not just client) -- real staff members carry 44-155 overdue
    # documents at a time, so days_overdue ties within the same client are
    # common, and without a full tie-break the list can shuffle between renders.
    recent_overdue = [
        _doc_row(it, i + 1)
        for i, it in enumerate(sorted(
            my_overdue,
            key=lambda it: (it.get("days_overdue", 0), it.get("client", "").lower(),
                            it.get("title", "").lower()))[:recent_n])
    ]

    return {
        "firm_name": firm_name,
        "generated_at": generated_at,
        "week_start": week_start.isoformat(),
        "staff": who,
        "found": bool(b["overdue"] or b["open"] or b["completed_week"]),
        "completed_week": b["completed_week"],
        "open": b["open"],
        "open_by_type": _types_list(b["open_by_type"]),
        "overdue": b["overdue"],
        "overdue_by_type": _types_list(b["overdue_by_type"]),
        "top_overdue": top_overdue,
        "recent_overdue": recent_overdue,
    }
