from datetime import date

import pandas as pd

import config
from data import analytics, importer, service
from data.store import Store

TODAY = date(2026, 6, 29)


# ---- return-type classification ----------------------------------------
def test_normalize_return_type_keeps_specific_value():
    # The exact type from the data is preserved (just trimmed), not bucketed.
    assert config.normalize_return_type("Tax: 1040") == "Tax: 1040"
    assert config.normalize_return_type("  Accounting/Bookkeeping ") == "Accounting/Bookkeeping"
    assert config.normalize_return_type("Tax: 1120S (S-Corp)") == "Tax: 1120S (S-Corp)"
    assert config.normalize_return_type("") == "Unclassified"
    assert config.normalize_return_type(None) == "Unclassified"


# ---- importer: project_key + return_type_raw ----------------------------
def _doc_df():
    return pd.DataFrame(
        {
            "Work ID": ["DOC-1", "DOC-2", "DOC-3"],
            "Client Name": ["Smith", "Smith", "Acme Inc"],
            "Document": ["W-2", "1099-INT", "Trial Balance"],
            "Return Type": ["1040 Individual", "1040 Individual", "1120 Corporation"],
            "Assignee": ["Sarah", "Sarah", "James"],
            "Status": ["Requested", "Requested", "Requested"],
            "Date Requested": ["2026-06-01", "2026-06-02", "2026-05-20"],
        }
    )


def test_apply_mapping_sets_project_and_type():
    m = importer.guess_mapping(list(_doc_df().columns))
    assert m["title"] == "Document"
    assert m["return_type"] == "Return Type"
    assert m["item_id"] == "Work ID"
    recs = importer.apply_mapping(_doc_df(), m)
    assert recs[0]["project_key"] == "c:smith"      # grouped by client
    assert recs[2]["project_key"] == "c:acme inc"
    assert recs[0]["return_type_raw"] == "1040 Individual"


# ---- analytics.build_projects ------------------------------------------
def _item(key, client, title, days, rtype_raw="", project_key=None, assignee=None):
    start = date(2026, 6, 29) - __import__("datetime").timedelta(days=days)
    it = {
        "item_key": key, "client": client, "title": title, "status": "Requested",
        "first_seen": start, "source_date": start, "return_type_raw": rtype_raw,
        "project_key": project_key or ("c:" + client.lower()),
        "assignee": assignee,
    }
    return it


def _enriched(items, overdue_days=14):
    return analytics.enrich_items(items, TODAY, overdue_days)


def test_build_projects_groups_and_classifies():
    items = _enriched([
        _item("d1", "Smith", "W-2", 5, "1040 Individual"),
        _item("d2", "Smith", "1099-INT", 30, "1040 Individual"),
        _item("d3", "Acme", "Trial Balance", 40, "1120 Corporation"),
    ])
    projects = analytics.build_projects(items, doc_states={}, project_states={})
    by_client = {p["client"]: p for p in projects}
    assert by_client["Smith"]["total_docs"] == 2
    # Specific type from the data is kept verbatim (no Individual/Business bucketing).
    assert by_client["Smith"]["return_type"] == "1040 Individual"
    assert by_client["Acme"]["return_type"] == "1120 Corporation"
    assert by_client["Smith"]["days_open"] == 30  # oldest doc
    assert by_client["Smith"]["received_docs"] == 0


def test_build_projects_received_counts_and_pct():
    items = _enriched([_item("d1", "Smith", "W-2", 5), _item("d2", "Smith", "1099", 6)])
    projects = analytics.build_projects(items, doc_states={"d1": True}, project_states={})
    p = projects[0]
    assert p["received_docs"] == 1
    assert p["outstanding_docs"] == 1
    assert p["pct_complete"] == 50
    assert p["outstanding_titles"] == ["1099"]


def test_manual_type_overrides_classification():
    items = _enriched([_item("d1", "Smith", "W-2", 5, "1040 Individual")])
    pstates = {"c:smith": {"return_type": "Tax: 1040", "completed": False}}
    p = analytics.build_projects(items, {}, pstates)[0]
    assert p["return_type"] == "Tax: 1040"  # manual override beats the data value


def test_completed_project_is_not_open():
    items = _enriched([_item("d1", "Smith", "W-2", 5)])
    pstates = {"c:smith": {"return_type": "Individual", "completed": True}}
    projects = analytics.build_projects(items, {}, pstates)
    assert projects[0]["open"] is False
    totals = analytics.project_totals(projects)
    assert totals["open_total"] == 0
    assert totals["completed_total"] == 1


def test_project_totals_split_by_specific_type():
    items = _enriched([
        _item("d1", "Smith", "W-2", 5, "Tax: 1040"),
        _item("d2", "Acme", "TB", 5, "Tax: 1120"),
        _item("d3", "Jones", "Ledger", 5, "Accounting/Bookkeeping"),
        _item("d4", "Doe", "W-2", 5, ""),  # no type -> Unclassified
    ])
    totals = analytics.project_totals(analytics.build_projects(items, {}, {}))
    assert totals["open_total"] == 4
    assert totals["type_count"] == 4
    assert totals["by_type"]["Tax: 1040"] == 1
    assert totals["by_type"]["Tax: 1120"] == 1
    assert totals["by_type"]["Accounting/Bookkeeping"] == 1
    assert totals["by_type"]["Unclassified"] == 1


def test_build_projects_excludes_unassigned_placeholder_from_multi_assignee_list():
    # service.dashboard_data normalizes former/inactive staff (see
    # config.UNASSIGNED_STAFF_NAMES) to "Unassigned" before this runs. That
    # placeholder must not show up as a real name alongside an actual assignee.
    items = _enriched([
        _item("d1", "Smith", "W-2", 5, assignee="Dana Whitfield"),
        _item("d2", "Smith", "1099", 6, assignee="Unassigned"),
    ])
    p = analytics.build_projects(items, {}, {})[0]
    assert p["assignees"] == ["Dana Whitfield"]
    assert p["assignee_label"] == "Dana Whitfield"


def test_build_projects_all_unassigned_falls_back_to_single_label():
    items = _enriched([
        _item("d1", "Smith", "W-2", 5, assignee="Unassigned"),
        _item("d2", "Smith", "1099", 6, assignee="(unassigned)"),
    ])
    p = analytics.build_projects(items, {}, {})[0]
    assert p["assignees"] == ["Unassigned"]
    assert p["assignee_label"] == "Unassigned"


def test_dashboard_data_normalizes_former_staff_to_unassigned(tmp_path, monkeypatch):
    # This rule is driven by config.UNASSIGNED_STAFF_NAMES, which comes from the
    # untracked local_config.py. Set it explicitly so the test is self-contained.
    monkeypatch.setattr(config, "UNASSIGNED_STAFF_NAMES", {"ivy fenwick"})
    s = Store(tmp_path / "unassigned.db")
    try:
        s.upsert_items([
            {"item_key": "d1", "assignee": "Ivy Fenwick", "client": "Smith",
             "title": "W-2", "status": "In Progress", "source_date": TODAY,
             "project_key": "c:smith", "return_type_raw": ""},
            {"item_key": "d2", "assignee": "Dana Whitfield", "client": "Acme",
             "title": "1099", "status": "In Progress", "source_date": TODAY,
             "project_key": "c:acme", "return_type_raw": ""},
        ], TODAY)
        data = service.dashboard_data(s, TODAY, overdue_days=14)
        by_client = {it["client"]: it for it in data["items"]}
        assert by_client["Smith"]["assignee"] == "Unassigned"   # was "Ivy Fenwick"
        assert by_client["Acme"]["assignee"] == "Dana Whitfield"  # untouched

        names = {it["assignee"] for it in data["items"]}
        assert "Ivy Fenwick" not in names
    finally:
        s.close()


# ---- store: doc + project state ----------------------------------------
def test_received_and_project_state_roundtrip(tmp_path):
    s = Store(tmp_path / "p.db")
    try:
        s.set_received("d1", True, TODAY)
        s.set_received("d2", False, TODAY)
        assert s.get_doc_states() == {"d1": True, "d2": False}

        s.set_project_type("c:smith", "Business")
        s.set_project_completed("c:smith", True, TODAY)
        states = s.get_project_states()
        assert states["c:smith"]["return_type"] == "Business"
        assert states["c:smith"]["completed"] is True
    finally:
        s.close()


def test_received_state_survives_reimport(tmp_path):
    """Checking a document off must persist across a later import."""
    s = Store(tmp_path / "p2.db")
    try:
        rec = {"item_key": "DOC-1", "assignee": "Sarah", "client": "Smith", "title": "W-2",
               "status": "Requested", "source_date": None, "project_key": "c:smith",
               "return_type_raw": "1040"}
        s.upsert_items([rec], date(2026, 6, 1))
        s.set_received("DOC-1", True, date(2026, 6, 5))
        s.upsert_items([rec], date(2026, 6, 12))  # re-import same doc
        assert s.get_doc_states()["DOC-1"] is True
    finally:
        s.close()
