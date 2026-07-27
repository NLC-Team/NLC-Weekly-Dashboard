"""Completion semantics used across the dashboard, Returns tab and Weekly Review.

Rules (service.dashboard_data + analytics.build_projects):
  - A document is COMPLETED only when its status is exactly "Completed" (or it's
    received / its return is manually completed).
  - A "Completed - <word>" status (Cancelled, Not a fit, Billed) is CLOSED-OTHER:
    it is folded out of overdue and the open counts just like a completion, but it
    does NOT count as completed and lands in the Returns "Completed - other" bucket.
  - Only genuinely-open work (neither of the above) counts as pending/overdue.
"""
from datetime import date, timedelta

from data import analytics, service
from data.store import Store

TODAY = date(2026, 6, 29)
OLD = TODAY - timedelta(days=30)   # older than any sane overdue threshold


def _rec(key, client, title, status):
    return {"item_key": key, "assignee": "A", "client": client, "title": title,
            "status": status, "source_date": OLD,
            "project_key": "c:" + client.lower(), "return_type_raw": ""}


def _build(tmp_path):
    """Clients covering every bucket: open-mix, pure completed, completed-other."""
    s = Store(tmp_path / "done.db")
    s.upsert_items([
        _rec("m1", "Mixed", "1040 Return", "Completed"),
        _rec("m2", "Mixed", "Bookkeeping", "In Progress"),
        _rec("p1", "Pure", "1065 Return", "Completed"),
        _rec("p2", "Pure", "State Return", "Completed"),
        _rec("o1", "Other", "1120 Return", "Completed"),
        _rec("o2", "Other", "Extension", "Completed - Cancelled"),
        _rec("x1", "Cancelled", "Notice", "Completed - Not a fit"),
    ], TODAY)
    return s


def _by_title(items):
    return {it["title"]: it for it in items}


def test_exact_completed_is_not_overdue(tmp_path):
    s = _build(tmp_path)
    try:
        data = service.dashboard_data(s, TODAY, overdue_days=14)
        items = _by_title(data["items"])
        assert items["1040 Return"]["overdue"] is False      # exact Completed -> done
        assert items["Bookkeeping"]["overdue"] is True        # genuinely open
        assert {it["title"] for it in data["overdue"]} == {"Bookkeeping"}
    finally:
        s.close()


def test_completed_variant_is_closed_but_not_done(tmp_path):
    s = _build(tmp_path)
    try:
        data = service.dashboard_data(s, TODAY, overdue_days=14)
        ext = _by_title(data["items"])["Extension"]          # "Completed - Cancelled"
        assert ext["overdue"] is False        # folded out of overdue
        assert ext["closed_other"] is True    # but not a real completion
        assert ext["completed"] is False
    finally:
        s.close()


def test_neither_completed_nor_variant_counts_as_open(tmp_path):
    s = _build(tmp_path)
    try:
        data = service.dashboard_data(s, TODAY, overdue_days=14)
        # Only "Bookkeeping" (In Progress) is genuinely open firm-wide.
        assert data["totals"]["total_pending"] == 1
        assert data["totals"]["total_overdue"] == 1
        rows = {r["assignee"]: r for r in data["per_assignee"]}
        assert rows["A"]["pending_count"] == 1
    finally:
        s.close()


def test_project_buckets(tmp_path):
    s = _build(tmp_path)
    try:
        data = service.dashboard_data(s, TODAY, overdue_days=14)
        p = {x["client"]: x for x in data["projects"]}
        # Mixed: has an open doc -> Open bucket.
        assert (p["Mixed"]["open"], p["Mixed"]["completed"], p["Mixed"]["closed_other"]) == (True, False, False)
        # Pure: every doc exactly "Completed" -> Completed bucket.
        assert (p["Pure"]["open"], p["Pure"]["completed"], p["Pure"]["closed_other"]) == (False, True, False)
        # Other: one Completed + one "Completed - Cancelled", nothing open -> Completed-other.
        assert (p["Other"]["open"], p["Other"]["completed"], p["Other"]["closed_other"]) == (False, False, True)
        # Cancelled: only a "Completed - Not a fit" doc -> Completed-other.
        assert p["Cancelled"]["closed_other"] is True
    finally:
        s.close()


def test_import_autocomplete_all_closed_other_stays_closed_other(tmp_path):
    # Reproduces the reported bug: re-importing an Excel with a client whose docs
    # are entirely "Completed - <word>" auto-completes the project via
    # apply_import_completion (the Settings "Completed statuses" list includes
    # those variants) -- but that must NOT promote the client into the real
    # "Completed" bucket; it should still show under "Completed - other".
    s = Store(tmp_path / "import_closed_other.db")
    try:
        completed_statuses = ["Completed", "Completed - Billed",
                               "Completed - Cancelled", "Completed - Not a fit"]
        r = s.upsert_items([_rec("c1", "AllCancelled", "1040 Return", "Completed - Cancelled")], TODAY)
        s.apply_import_completion(completed_statuses, r["new_keys"], TODAY)
        st = s.get_project_states()["c:allcancelled"]
        assert st["completed"] and st["completed_source"] == "import"  # sanity

        data = service.dashboard_data(s, TODAY, overdue_days=14)
        it = _by_title(data["items"])["1040 Return"]
        assert it["overdue"] is False
        assert it["closed_other"] is True
        assert it["completed"] is False

        p = {x["client"]: x for x in data["projects"]}["AllCancelled"]
        assert (p["open"], p["completed"], p["closed_other"]) == (False, False, True)
    finally:
        s.close()


def test_manual_complete_still_forces_completed_bucket(tmp_path):
    # A human explicitly clicking "Complete" always means a real completion,
    # even if the underlying doc status is a "Completed - <word>" variant.
    s = Store(tmp_path / "manual_closed_other.db")
    try:
        s.upsert_items([_rec("c1", "HandDone", "1040 Return", "Completed - Cancelled")], TODAY)
        s.set_project_completed("c:handdone", True, TODAY, source="manual")

        data = service.dashboard_data(s, TODAY, overdue_days=14)
        it = _by_title(data["items"])["1040 Return"]
        assert it["completed"] is True
        assert it["closed_other"] is False

        p = {x["client"]: x for x in data["projects"]}["HandDone"]
        assert (p["open"], p["completed"], p["closed_other"]) == (False, True, False)
    finally:
        s.close()


def test_items_list_keeps_all_rows(tmp_path):
    # The full item list keeps completed + closed-other rows for the Weekly Review.
    s = _build(tmp_path)
    try:
        data = service.dashboard_data(s, TODAY, overdue_days=14)
        assert len(data["items"]) == 7
    finally:
        s.close()


# ---- completed is counted per CLIENT, never per document -------------------
def test_completed_counted_once_per_client(tmp_path):
    # A client that finished SEVERAL documents is ONE completed return, not one
    # per document -- the rule the whole dashboard and Weekly Review share.
    s = _build(tmp_path)
    try:
        data = service.dashboard_data(s, TODAY, overdue_days=14)
        # "Pure" has two completed documents but counts once.
        assert len([it for it in data["items"] if it["completed"]]) > 1
        assert data["project_totals"]["completed_total"] == 1
        by_staff = analytics.completed_projects_by_assignee(data["projects"])
        assert [p["client"] for p in by_staff["A"]] == ["Pure"]
    finally:
        s.close()


def test_partly_finished_client_is_not_completed(tmp_path):
    # "Mixed" has one Completed doc and one still open -> NOT a completed client.
    # Only a client whose work is all done (or hand-completed) counts.
    s = _build(tmp_path)
    try:
        data = service.dashboard_data(s, TODAY, overdue_days=14)
        by_staff = analytics.completed_projects_by_assignee(data["projects"])
        assert "Mixed" not in {p["client"] for p in by_staff["A"]}
        # ...and neither does a client closed only via "Completed - <word>".
        assert "Other" not in {p["client"] for p in by_staff["A"]}
        assert "Cancelled" not in {p["client"] for p in by_staff["A"]}
    finally:
        s.close()


def test_completed_client_listed_under_every_staff_member(tmp_path):
    # A return worked by two people shows up once for each of them (same
    # attribution the Weekly Review uses), still one row per client per person.
    s = Store(tmp_path / "shared.db")
    try:
        recs = [_rec("s1", "Shared", "1040 Return", "Completed"),
                _rec("s2", "Shared", "State Return", "Completed")]
        recs[1]["assignee"] = "B"
        s.upsert_items(recs, TODAY)
        data = service.dashboard_data(s, TODAY, overdue_days=14)
        by_staff = analytics.completed_projects_by_assignee(data["projects"])
        assert [p["client"] for p in by_staff["A"]] == ["Shared"]
        assert [p["client"] for p in by_staff["B"]] == ["Shared"]
        # One distinct completed client firm-wide, despite two staff rows.
        assert data["project_totals"]["completed_total"] == 1
    finally:
        s.close()


def test_title_label_bundles_a_clients_documents(tmp_path):
    s = _build(tmp_path)
    try:
        data = service.dashboard_data(s, TODAY, overdue_days=14)
        p = {x["client"]: x for x in data["projects"]}["Pure"]
        assert p["title_label"] == "1065 Return, State Return"
    finally:
        s.close()


def test_group_by_client_owner_orders_named_then_unowned():
    rows = [{"client_owner": "Zoe"}, {"client_owner": None},
            {"client_owner": "alan"}, {"client_owner": "  "}]
    groups = analytics.group_by_client_owner(rows)
    assert [name for name, _ in groups] == ["alan", "Zoe", "No owner"]
    assert len(groups[-1][1]) == 2      # None and blank share the No-owner bucket
