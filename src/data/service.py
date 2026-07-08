"""Orchestration tying importer + store + analytics together.

Keeping this glue out of the UI makes the whole import-and-aggregate pipeline
testable without a window on screen.
"""
from __future__ import annotations

from datetime import date

from data import analytics, importer
from data.store import Store


def import_csv(store: Store, path: str, mapping: dict, pending_statuses: list[str], today: date) -> dict:
    """Run a full import: parse -> filter -> persist history. Returns stats."""
    df = importer.load_csv(path)
    records = importer.apply_mapping(df, mapping)
    pending = importer.filter_pending(records, pending_statuses)

    stats = store.upsert_items(pending, today)
    file_name = path.replace("\\", "/").split("/")[-1]
    store.record_import(file_name, len(df), len(pending), today)
    stats["rows"] = len(df)
    stats["pending"] = len(pending)
    return stats


def dashboard_data(store: Store, today: date, overdue_days: int) -> dict:
    """Everything the views need, computed from current history + settings."""
    items = analytics.enrich_items(store.active_items(), today, overdue_days)
    doc_states = store.get_doc_states()
    project_states = store.get_project_states()
    # A document stops being "action needed" once it's marked received from the
    # client or its whole return is marked completed. Fold that into each item's
    # overdue flag so completing/receiving work on the Returns & Bookkeeping page
    # automatically drops it off the Overdue tab and its counts — no re-import.
    completed_projects = {k for k, v in project_states.items() if v.get("completed")}
    for it in items:
        it["received"] = bool(doc_states.get(it["item_key"], False))
        it["completed"] = it.get("project_key") in completed_projects
        if it["received"] or it["completed"]:
            it["overdue"] = False
            it["days_overdue"] = 0
    projects = analytics.build_projects(items, doc_states, project_states)
    # Give every item the same effective return type as its project (a manual
    # reclassification on Returns & Bookkeeping wins over the raw CSV value),
    # so Overview/Overdue/Staff views that group by type stay in sync with it.
    type_by_pkey = {p["project_key"]: p["return_type"] for p in projects}
    for it in items:
        pkey = it.get("project_key") or ("c:" + it["client"].strip().lower())
        it["return_type"] = type_by_pkey[pkey]
    return {
        "items": items,
        "totals": analytics.totals(items),
        "per_assignee": analytics.per_assignee(items),
        "overdue": analytics.overdue_items(items),
        "age_distribution": analytics.age_distribution(items),
        "projects": projects,
        "project_totals": analytics.project_totals(projects),
    }
