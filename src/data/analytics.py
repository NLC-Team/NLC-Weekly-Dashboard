"""Pure analytics functions.

These take plain Python data (lists of item dicts + parameters) and return plain
data. No database, no UI, no global state -- which is exactly why they are easy
to unit-test. A "pending item" dict is expected to have these keys:

    assignee, client, title, status   -> str
    first_seen                         -> datetime.date (when we first saw it pending)
    source_date                        -> datetime.date | None (date from the CSV, if any)

`enrich_items` adds `age_days` and `overdue` to each item; the aggregation
helpers below assume that enrichment has already happened.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Iterable

from config import AGE_BUCKETS, normalize_return_type


def status_is_done(status) -> bool:
    """True when a document is genuinely COMPLETED.

    Matches ONLY the exact status "Completed" (case-insensitive, trimmed). A
    "Completed - *" variant (Cancelled, Not a fit, Billed, ...) does NOT count as
    done -- those are "closed for another reason", see status_is_closed_other.
    Blank/None -> not done.
    """
    return (status or "").strip().lower() == "completed"


def status_is_closed_other(status) -> bool:
    """True for a "Completed - <something>" status (Cancelled, Not a fit, Billed).

    These contain the word "completed" but are not the plain "Completed" that
    means the work was finished. They are closed out (no longer action-needed)
    but are counted separately from real completions.
    """
    s = (status or "").strip().lower()
    return "completed" in s and s != "completed"


def item_age_days(item: dict, today: date) -> int:
    """Days an item has been open.

    Prefers the real date from the CSV (`source_date`); falls back to how long we
    have been tracking the item (`first_seen`). Never negative.
    """
    basis = item.get("source_date") or item.get("first_seen")
    if basis is None:
        return 0
    return max(0, (today - basis).days)


def enrich_items(items: Iterable[dict], today: date, overdue_days: int) -> list[dict]:
    """Return copies of items with `age_days`, `overdue`, and `days_overdue` filled in.

    Overdue is judged from the document's own DUE DATE (the export's start date
    to due date span) when the import provided one: a document goes overdue the
    day `today` reaches its `due_date`, and `days_overdue` is `today - due_date`
    (never negative). A document with no due date falls back to the age-based
    rule -- `age_days` (from the start/source date) past the configurable
    `overdue_days` threshold -- so nothing regresses for rows the export doesn't
    date. Either way it's recomputed live against `today` every load, so a
    document flips into (or out of) overdue as the calendar moves, not frozen at
    import time. Status is checked separately, upstream (service.dashboard_data):
    a completed/received/closed document is folded out of overdue regardless of
    its due date.
    """
    out = []
    for it in items:
        age = item_age_days(it, today)
        enriched = dict(it)
        enriched["age_days"] = age
        due = it.get("due_date")
        if due is not None:
            days_over = (today - due).days
            enriched["overdue"] = days_over >= 0
            enriched["days_overdue"] = max(0, days_over)
        else:
            enriched["overdue"] = age > overdue_days
            enriched["days_overdue"] = max(0, age - overdue_days)
        out.append(enriched)
    return out


def totals(items: list[dict]) -> dict:
    """Headline numbers for the KPI cards."""
    pending = len(items)
    overdue = sum(1 for it in items if it.get("overdue"))
    staff = len({it["assignee"] for it in items})
    ages = [it["age_days"] for it in items]
    avg_age = round(sum(ages) / len(ages)) if ages else 0
    return {
        "total_pending": pending,
        "total_overdue": overdue,
        "staff_count": staff,
        "avg_age": avg_age,
    }


def per_assignee(items: list[dict]) -> list[dict]:
    """Workload per staff member, sorted by who has the most on their plate.

    Tie-breaks: more overdue first, then higher average age.
    """
    groups: dict[str, list[dict]] = {}
    for it in items:
        groups.setdefault(it["assignee"], []).append(it)

    rows = []
    for assignee, group in groups.items():
        ages = [it["age_days"] for it in group]
        oldest = max(group, key=lambda x: x["age_days"]) if group else None
        # How many of each specific statement type make up this person's workload.
        # `return_type` (set by service.dashboard_data) reflects any manual
        # reclassification on Returns & Bookkeeping; fall back to the raw CSV
        # value when called directly on items that don't have it (e.g. tests).
        type_counts = Counter(
            it.get("return_type") or normalize_return_type(it.get("return_type_raw"))
            for it in group
        )
        rows.append(
            {
                "assignee": assignee,
                "pending_count": len(group),
                "overdue_count": sum(1 for it in group if it.get("overdue")),
                "avg_age": round(sum(ages) / len(ages)) if ages else 0,
                "max_age": max(ages) if ages else 0,
                "oldest_client": oldest["client"] if oldest else "",
                # [(type, count), ...] most common first, for the workload table.
                "type_breakdown": type_counts.most_common(),
            }
        )

    rows.sort(key=lambda r: (r["pending_count"], r["overdue_count"], r["avg_age"]), reverse=True)
    return rows


def overdue_items(items: list[dict]) -> list[dict]:
    """All overdue items, oldest first -- the per-person 'taking too long' list."""
    od = [it for it in items if it.get("overdue")]
    od.sort(key=lambda x: x["age_days"], reverse=True)
    return od


def build_projects(items: list[dict], doc_states: dict, project_states: dict) -> list[dict]:
    """Group enriched document-items into tax-return projects.

    `items` must already be enriched (have `age_days`/`overdue`). `doc_states`
    maps item_key -> received(bool); `project_states` maps project_key ->
    {return_type, completed}. Returns one dict per project, open ones first.
    """
    groups: dict[str, list[dict]] = {}
    for it in items:
        groups.setdefault(it.get("project_key") or ("c:" + it["client"].strip().lower()), []).append(it)

    projects = []
    for pkey, docs in groups.items():
        state = project_states.get(pkey, {})
        # Effective return type: a manual override wins, else the specific type
        # from the data (kept verbatim, e.g. "Tax: 1040"), else "Unclassified".
        rtype = state.get("return_type")
        if not rtype:
            raw = next((d.get("return_type_raw") for d in docs if d.get("return_type_raw")), None)
            rtype = normalize_return_type(raw)
        manual_or_import_complete = bool(state.get("completed", False))
        # A human clicking "Complete" always means a real completion. An
        # import-completion does NOT get the same free pass: apply_import_completion
        # auto-completes a client once every doc's status is in the configured
        # "Completed statuses" list, which includes "Completed - <word>" variants --
        # so an all-closed-other client gets auto-completed on every import too. That
        # must not promote it into the real Completed bucket (see per-doc check below).
        manual_complete = manual_or_import_complete and state.get("completed_source") == "manual"
        # Owner of the client (first non-empty across the return's docs) and when it
        # was completed — used by the Weekly Review's "completed this week" section.
        client_owner = next((d.get("client_owner") for d in docs if d.get("client_owner")), None)

        # Bucket the return: OPEN while any document still needs action; else
        # COMPLETED when every finished document is a real completion (exact
        # "Completed" / received / hand-completed); else CLOSED-OTHER, i.e. it was
        # closed out only via a "Completed - <word>" status (Cancelled / Not a fit /
        # Billed). Only a MANUAL project completion always reads as completed.
        has_open = has_closed_other = False
        for d in docs:
            recv = bool(doc_states.get(d["item_key"], False))
            if status_is_done(d["status"]) or recv or manual_complete:
                continue                      # a real completion
            if status_is_closed_other(d["status"]):
                has_closed_other = True
            elif not manual_or_import_complete:
                has_open = True
            # else: import-completed via a status this helper doesn't recognize as
            # done/closed-other -- apply_import_completion only sets this flag when
            # every doc's status is in the configured list, so treat it as done.
        if manual_complete or not (has_open or has_closed_other):
            completed, closed_other = True, False
        elif has_open:
            completed, closed_other = False, False
        else:
            completed, closed_other = False, True

        doc_rows = []
        received_count = 0
        for d in docs:
            recv = bool(doc_states.get(d["item_key"], False))
            received_count += 1 if recv else 0
            doc_rows.append(
                {
                    "item_key": d["item_key"],
                    "title": d["title"],
                    "status": d["status"],
                    "age_days": d["age_days"],
                    "overdue": d["overdue"],
                    "received": recv,
                }
            )
        doc_rows.sort(key=lambda x: -x["age_days"])  # oldest first; received state doesn't shift position

        client = next((d["client"] for d in docs if d["client"]), "(no client)")
        days_open = max((d["age_days"] for d in docs), default=0)

        # Responsible employee(s): a return can span docs owned by different
        # staff, so keep the full de-duplicated list plus a compact label. The
        # importer stores a missing assignee as the literal "(unassigned)"
        # (importer.py); service.dashboard_data normalizes former/inactive staff
        # (config.UNASSIGNED_STAFF_NAMES) to "Unassigned" before this runs --
        # treat both as unassigned so neither shows up as a real name in a
        # multi-assignee project, and default to a single "Unassigned" label
        # (rendered as plain text, never a link) when nothing real is left.
        assignees = sorted({a for d in docs
                            for a in [(d.get("assignee") or "").strip()]
                            if a and a.lower() not in ("(unassigned)", "unassigned")})
        if not assignees:
            assignees = ["Unassigned"]
        assignee_label = (", ".join(assignees) if len(assignees) <= 2
                          else f"{assignees[0]} +{len(assignees) - 1}")

        # When the return "opened": earliest real date across its docs (the CSV
        # date if present, else when we first saw it), mirroring item_age_days.
        opened_dates = [d.get("source_date") or d.get("first_seen")
                        for d in docs if (d.get("source_date") or d.get("first_seen"))]
        opened_on = min(opened_dates) if opened_dates else None

        projects.append(
            {
                "project_key": pkey,
                "client": client,
                "return_type": rtype,
                "client_owner": client_owner,
                "assignees": assignees,
                "assignee_label": assignee_label,
                "opened_on": opened_on,
                "opened_year": opened_on.year if opened_on else None,
                "opened_month": opened_on.month if opened_on else None,
                "completed": completed,
                "closed_other": closed_other,
                "completed_at": state.get("completed_at"),
                "open": not (completed or closed_other),
                "documents": doc_rows,
                "total_docs": len(doc_rows),
                "received_docs": received_count,
                "outstanding_docs": len(doc_rows) - received_count,
                "outstanding_titles": [d["title"] for d in doc_rows if not d["received"]],
                "pct_complete": round(100 * received_count / len(doc_rows)) if doc_rows else 0,
                "days_open": days_open,
                # A client reads as Overdue only while NOTHING has come in yet.
                # The moment any document is marked off (received), the client is
                # being worked, so it shows Open — not Overdue. Mirrors the
                # "mark a document -> client Open" rule (received also reopens a
                # closed client, see store.set_received).
                "overdue": received_count == 0 and any(d["overdue"] for d in doc_rows),
                # How long the project itself has read as overdue, driven by its
                # WORST (most overdue) document — mirrors `days_open` using max(),
                # since a single old document is what put the whole client into
                # Overdue in the first place, however fresh its other documents are.
                "days_overdue": max((d.get("days_overdue", 0) for d in docs if d.get("overdue")),
                                   default=0),
            }
        )

    # Open returns first, then completed, then closed-other; alphabetical within each.
    projects.sort(key=lambda p: (not p["open"], p["closed_other"], p["client"].lower()))
    return projects


def project_totals(projects: list[dict]) -> dict:
    """Headline numbers for the tax-return tracker.

    `by_type` counts open projects per specific type (e.g. {"Tax: 1040": 12,
    "Accounting/Bookkeeping": 5, ...}), highest first — it grows automatically as
    new types appear in the data, so nothing here is hard-coded to a fixed set.
    """
    open_projects = [p for p in projects if p["open"]]
    by_type: dict[str, int] = {}
    for p in open_projects:
        by_type[p["return_type"]] = by_type.get(p["return_type"], 0) + 1
    by_type = dict(sorted(by_type.items(), key=lambda kv: (-kv[1], kv[0].lower())))
    pcts = [p["pct_complete"] for p in open_projects]
    return {
        "open_total": len(open_projects),
        "by_type": by_type,
        "type_count": len(by_type),
        "completed_total": sum(1 for p in projects if p["completed"]),
        "closed_other_total": sum(1 for p in projects if p.get("closed_other")),
        "avg_pct_complete": round(sum(pcts) / len(pcts)) if pcts else 0,
        "docs_outstanding": sum(p["outstanding_docs"] for p in open_projects),
    }


def age_distribution(items: list[dict], buckets: list[int] | None = None) -> list[tuple[str, int]]:
    """Counts of items per age bucket, for the distribution chart."""
    buckets = buckets or AGE_BUCKETS
    labels = []
    lo = 0
    for hi in buckets:
        labels.append((f"{lo}-{hi}d", lo, hi))
        lo = hi + 1
    labels.append((f"{buckets[-1] + 1}d+", buckets[-1] + 1, None))

    counts = []
    for label, low, high in labels:
        n = sum(
            1
            for it in items
            if it["age_days"] >= low and (high is None or it["age_days"] <= high)
        )
        counts.append((label, n))
    return counts
