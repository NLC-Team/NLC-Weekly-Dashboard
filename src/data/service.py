"""Orchestration tying importer + store + analytics together.

Keeping this glue out of the UI makes the whole import-and-aggregate pipeline
testable without a window on screen.
"""
from __future__ import annotations

from datetime import date

import config
from data import analytics, importer
from data.store import Store


def import_csv(store: Store, path: str, mapping: dict, pending_statuses: list[str],
               today: date, completed_statuses: list[str] | None = None) -> dict:
    """Run a full import: parse -> persist EVERY row -> auto-complete. Returns stats.

    No status filtering: every mapped row is stored, so no data is silently
    dropped (the old 'pending statuses' drop is gone). `pending_statuses` is kept
    only for signature/back-compat and is ignored here. Returns whose active
    documents are ALL in a configured *completed* status are then moved to the
    Done tab (store.apply_import_completion)."""
    completed_statuses = completed_statuses or []
    df = importer.load_csv(path)
    records = importer.apply_mapping(df, mapping)

    # Import every row — nothing is filtered out.
    stats = store.upsert_items(records, today)
    sync = store.apply_import_completion(completed_statuses, stats.get("new_keys", []), today)
    file_name = path.replace("\\", "/").split("/")[-1]
    store.record_import(file_name, len(df), len(records), today)
    stats["rows"] = len(df)
    stats["imported"] = len(records)
    stats["auto_completed"] = sync["completed"]
    stats["reopened"] = sync["reopened"]
    return stats


def dashboard_data(store: Store, today: date, overdue_days: int) -> dict:
    """Everything the views need, computed from current history + settings."""
    # Hide one assignee's property line-items from the WHOLE dashboard (see
    # config.is_hidden_item). Filtered here, at the single source every view
    # reads, so Overview/Overdue/Returns/Staff/Weekly Review all agree.
    active = [it for it in store.active_items()
              if not config.is_hidden_item(it.get("assignee"), it.get("client"), it.get("title"))]
    items = analytics.enrich_items(active, today, overdue_days)
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
