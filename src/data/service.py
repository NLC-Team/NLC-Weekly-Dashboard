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
    # Former/inactive staff (config.UNASSIGNED_STAFF_NAMES): their documents
    # still count everywhere, just under "Unassigned" instead of their name.
    # Same single-source principle as the hide filter above.
    for it in active:
        it["assignee"] = config.normalize_assignee(it.get("assignee"))
    items = analytics.enrich_items(active, today, overdue_days)
    doc_states = store.get_doc_states()
    project_states = store.get_project_states()
    # Classify every document by how "finished" it is:
    #   completed    -> exactly "Completed" (analytics.status_is_done), OR received
    #                   from the client, OR its return is manually completed, OR
    #                   its return is import-completed and this doc isn't itself a
    #                   "Completed - <word>" variant.
    #   closed_other -> a "Completed - <word>" status (Cancelled / Not a fit /
    #                   Billed): closed out, but NOT a real completion. A project
    #                   being import-completed does NOT override this -- the
    #                   Settings "Completed statuses" list that drives
    #                   apply_import_completion includes these variants too, so an
    #                   all-closed-other client gets auto-completed on every import;
    #                   only a MANUAL "Complete" click (a human decision) should
    #                   promote such a document into a real completion.
    # Both completed and closed_other are "closed": folded out of the overdue flag
    # AND out of the open-work aggregates below, so neither nags on the Overdue tab
    # or in any count. Only genuinely-open work remains. The full `items` list is
    # kept intact (every row) for the Weekly Review's recent/completed sections.
    completed_projects = {k for k, v in project_states.items() if v.get("completed")}
    manual_projects = {k for k, v in project_states.items()
                        if v.get("completed") and v.get("completed_source") == "manual"}
    for it in items:
        it["received"] = bool(doc_states.get(it["item_key"], False))
        pkey = it.get("project_key")
        own_closed_other = analytics.status_is_closed_other(it.get("status"))
        it["completed"] = (
            analytics.status_is_done(it.get("status"))
            or it["received"]
            or pkey in manual_projects
            or (pkey in completed_projects and not own_closed_other)
        )
        it["closed_other"] = own_closed_other and not it["completed"]
        it["closed"] = it["completed"] or it["closed_other"]
        if it["closed"]:
            it["overdue"] = False
            it["days_overdue"] = 0
    # Open-work aggregates count only documents that still need action.
    open_items = [it for it in items if not it["closed"]]
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
        "totals": analytics.totals(open_items),
        "per_assignee": analytics.per_assignee(open_items),
        "overdue": analytics.overdue_items(open_items),
        "age_distribution": analytics.age_distribution(open_items),
        "projects": projects,
        "project_totals": analytics.project_totals(projects),
    }
