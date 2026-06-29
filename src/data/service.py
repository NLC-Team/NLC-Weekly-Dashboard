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
    projects = analytics.build_projects(items, store.get_doc_states(), store.get_project_states())
    return {
        "items": items,
        "totals": analytics.totals(items),
        "per_assignee": analytics.per_assignee(items),
        "overdue": analytics.overdue_items(items),
        "age_distribution": analytics.age_distribution(items),
        "projects": projects,
        "project_totals": analytics.project_totals(projects),
    }
