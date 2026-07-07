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

    `days_overdue` is how many days past the overdue threshold an item is
    (`age_days - overdue_days`, never negative). Because it derives from
    `age_days`, which is measured against `today`, it advances by one every
    calendar day the item stays open — it's not frozen at import time.
    """
    out = []
    for it in items:
        age = item_age_days(it, today)
        enriched = dict(it)
        enriched["age_days"] = age
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
        type_counts = Counter(normalize_return_type(it.get("return_type_raw")) for it in group)
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
        completed = bool(state.get("completed", False))

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
        # (importer.py), so treat that as unassigned too and normalize to a
        # single "Unassigned" label (rendered as plain text, never a link).
        assignees = sorted({a for d in docs
                            for a in [(d.get("assignee") or "").strip()]
                            if a and a.lower() != "(unassigned)"})
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
                "assignees": assignees,
                "assignee_label": assignee_label,
                "opened_on": opened_on,
                "opened_year": opened_on.year if opened_on else None,
                "opened_month": opened_on.month if opened_on else None,
                "completed": completed,
                "open": not completed,
                "documents": doc_rows,
                "total_docs": len(doc_rows),
                "received_docs": received_count,
                "outstanding_docs": len(doc_rows) - received_count,
                "outstanding_titles": [d["title"] for d in doc_rows if not d["received"]],
                "pct_complete": round(100 * received_count / len(doc_rows)) if doc_rows else 0,
                "days_open": days_open,
                "overdue": any(d["overdue"] for d in doc_rows),
            }
        )

    projects.sort(key=lambda p: (p["completed"], p["client"].lower()))
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
