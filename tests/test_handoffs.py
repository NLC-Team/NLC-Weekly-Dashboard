"""Handoff detection: a reassignment orphans the previous assignee's row.

An item_key hashes client|title|assignee (see importer._make_key), so Karbon
reassigning a document inserts a NEW row and leaves the old one active forever.
detect_handoffs finds those orphans by grouping active items on (client, title)
and recording the ones a same-titled, differently-assigned row replaced.
"""
from datetime import date, timedelta

from data.store import Store

DAY1 = date(2026, 6, 29)
DAY2 = DAY1 + timedelta(days=1)


def _rec(key, client, title, assignee, status="In Progress"):
    return {"item_key": key, "assignee": assignee, "client": client,
            "title": title, "status": status, "source_date": DAY1,
            "project_key": "c:" + client.lower(), "return_type_raw": ""}


def test_reassignment_records_a_handoff(tmp_path):
    s = Store(tmp_path / "h.db")
    try:
        s.upsert_items([_rec("k_a", "Acme", "1040 Return", "Alice")], DAY1)
        # DAY2's export has the same work under Bob, so it hashes to a new key.
        s.upsert_items([_rec("k_b", "Acme", "1040 Return", "Bob")], DAY2)
        assert s.detect_handoffs(DAY2) == 1

        h = s.get_handoffs()
        assert list(h) == ["k_a"]                  # the STALE row is the one recorded
        assert h["k_a"]["from_assignee"] == "Alice"
        assert h["k_a"]["to_assignee"] == "Bob"
        assert h["k_a"]["handed_at"] == "2026-06-30"
    finally:
        s.close()


def test_detection_is_idempotent(tmp_path):
    s = Store(tmp_path / "h.db")
    try:
        s.upsert_items([_rec("k_a", "Acme", "1040 Return", "Alice")], DAY1)
        s.upsert_items([_rec("k_b", "Acme", "1040 Return", "Bob")], DAY2)
        assert s.detect_handoffs(DAY2) == 1
        assert s.detect_handoffs(DAY2) == 0        # re-running records nothing new
        assert len(s.get_handoffs()) == 1
    finally:
        s.close()


def test_both_assignees_in_the_same_import_is_not_a_handoff(tmp_path):
    # Two people genuinely holding same-titled work for one client. Neither row
    # went stale, so neither is a handoff.
    s = Store(tmp_path / "h.db")
    try:
        s.upsert_items([_rec("k_a", "Acme", "1040 Return", "Alice"),
                        _rec("k_b", "Acme", "1040 Return", "Bob")], DAY2)
        assert s.detect_handoffs(DAY2) == 0
        assert s.get_handoffs() == {}
    finally:
        s.close()


def test_vanished_row_with_no_sibling_is_not_a_handoff(tmp_path):
    # The work disappeared from the export entirely (deleted in Karbon). Nobody
    # took it over, so nobody is credited and the row is left alone.
    s = Store(tmp_path / "h.db")
    try:
        s.upsert_items([_rec("k_a", "Acme", "1040 Return", "Alice")], DAY1)
        s.upsert_items([_rec("k_z", "Other", "1120 Return", "Bob")], DAY2)
        assert s.detect_handoffs(DAY2) == 0
    finally:
        s.close()


def test_already_finished_row_is_not_re_credited(tmp_path):
    # A genuinely completed row that later drops out keeps its real completion
    # and must never also be credited as a handoff.
    s = Store(tmp_path / "h.db")
    try:
        s.upsert_items([_rec("k_a", "Acme", "1040 Return", "Alice", "Completed"),
                        _rec("k_c", "Beta", "1120 Return", "Carol",
                             "Completed - Cancelled")], DAY1)
        s.upsert_items([_rec("k_b", "Acme", "1040 Return", "Bob"),
                        _rec("k_d", "Beta", "1120 Return", "Dave")], DAY2)
        assert s.detect_handoffs(DAY2) == 0
    finally:
        s.close()


def test_client_and_title_match_ignores_case_and_spacing(tmp_path):
    s = Store(tmp_path / "h.db")
    try:
        s.upsert_items([_rec("k_a", "Acme  ", "1040 Return", "Alice")], DAY1)
        s.upsert_items([_rec("k_b", "ACME", " 1040 return", "Bob")], DAY2)
        assert s.detect_handoffs(DAY2) == 1
    finally:
        s.close()


def test_several_new_assignees_are_all_recorded(tmp_path):
    # Rare (1 case in the live data): the stale row was replaced by rows under
    # more than one assignee. Record all of them so the label is truthful.
    s = Store(tmp_path / "h.db")
    try:
        s.upsert_items([_rec("k_a", "Acme", "1040 Return", "Alice")], DAY1)
        s.upsert_items([_rec("k_b", "Acme", "1040 Return", "Bob"),
                        _rec("k_c", "Acme", "1040 Return", "Carol")], DAY2)
        assert s.detect_handoffs(DAY2) == 1
        assert s.get_handoffs()["k_a"]["to_assignee"] == "Bob, Carol"
    finally:
        s.close()


def test_import_csv_detects_handoffs_and_reports_the_count(tmp_path):
    """A real import run records handoffs and reports them in its stats."""
    import pandas as pd

    from data import service

    s = Store(tmp_path / "h.db")
    try:
        mapping = {"assignee": "Assignee", "client": "Client Name",
                   "title": "Work Title", "status": "Status"}

        def _write(path, assignee):
            pd.DataFrame([{"Assignee": assignee, "Client Name": "Acme",
                           "Work Title": "1040 Return",
                           "Status": "In Progress"}]).to_csv(path, index=False)

        day1 = tmp_path / "day1.csv"
        day2 = tmp_path / "day2.csv"
        _write(day1, "Alice")
        _write(day2, "Bob")

        first = service.import_csv(s, str(day1), mapping, [], DAY1)
        assert first["handoffs"] == 0

        second = service.import_csv(s, str(day2), mapping, [], DAY2)
        assert second["handoffs"] == 1
        assert [h["from_assignee"] for h in s.get_handoffs().values()] == ["Alice"]
    finally:
        s.close()


def test_handed_off_item_is_closed_but_not_completed(tmp_path):
    from data import service

    s = Store(tmp_path / "h.db")
    try:
        s.upsert_items([_rec("k_a", "Acme", "1040 Return", "Alice")], DAY1)
        s.upsert_items([_rec("k_b", "Acme", "1040 Return", "Bob")], DAY2)
        s.detect_handoffs(DAY2)

        data = service.dashboard_data(s, DAY2, overdue_days=14)
        by_key = {it["item_key"]: it for it in data["items"]}

        stale = by_key["k_a"]
        assert stale["handed_off"] is True
        assert stale["handed_to"] == "Bob"
        assert stale["handed_at"] == "2026-06-30"
        assert stale["closed"] is True          # off Alice's plate
        assert stale["completed"] is False      # but NOT a completed return
        assert stale["closed_other"] is False

        live = by_key["k_b"]
        assert live["handed_off"] is False
        assert live["closed"] is False
    finally:
        s.close()


def test_handed_off_item_leaves_the_open_and_overdue_counts(tmp_path):
    from data import service

    s = Store(tmp_path / "h.db")
    try:
        # source_date is DAY1 - 30, so both rows are well past the 14-day rule.
        old = {"source_date": DAY1 - timedelta(days=30)}
        s.upsert_items([{**_rec("k_a", "Acme", "1040 Return", "Alice"), **old}], DAY1)
        s.upsert_items([{**_rec("k_b", "Acme", "1040 Return", "Bob"), **old}], DAY2)

        before = service.dashboard_data(s, DAY2, overdue_days=14)
        assert before["totals"]["total_pending"] == 2      # double-counted today
        assert {r["assignee"] for r in before["per_assignee"]} == {"Alice", "Bob"}

        s.detect_handoffs(DAY2)
        after = service.dashboard_data(s, DAY2, overdue_days=14)
        assert after["totals"]["total_pending"] == 1       # counted once now
        assert after["totals"]["total_overdue"] == 1
        assert {it["item_key"] for it in after["overdue"]} == {"k_b"}
        assert {r["assignee"] for r in after["per_assignee"]} == {"Bob"}
    finally:
        s.close()


def test_handoff_does_not_change_the_per_client_completed_count(tmp_path):
    # The firm-wide completed figure is ONE per client and must not move.
    from data import service

    s = Store(tmp_path / "h.db")
    try:
        s.upsert_items([_rec("k_a", "Acme", "1040 Return", "Alice")], DAY1)
        s.upsert_items([_rec("k_b", "Acme", "1040 Return", "Bob")], DAY2)
        s.detect_handoffs(DAY2)

        data = service.dashboard_data(s, DAY2, overdue_days=14)
        assert data["project_totals"]["completed_total"] == 0
    finally:
        s.close()


# ---- Weekly Review credit ---------------------------------------------------
# These build review data directly (like tests/test_review.py) rather than
# through the Store, so the window arithmetic is explicit. test_review's `_item`
# and `_data` are reused so the item shape and the dashboard_data mirror can
# never drift between the two files.
#
# NOTE: `tests/` has no __init__.py, so pytest puts that directory itself on
# sys.path -- import the module bare (`from test_review import ...`), never as
# `tests.test_review`, which does not resolve.
from datetime import datetime           # noqa: E402

from data import review as review_mod    # noqa: E402
from test_review import _data as _review_data, _item as _review_item  # noqa: E402

REVIEW_TODAY = date(2026, 6, 29)


def _ritem(key, client, title, assignee, days=30, status="In Progress",
           completed_date=None):
    return _review_item(key, client, title, days, assignee=assignee,
                        status=status, completed_date=completed_date)


def _rdata(items, handoffs=None):
    return _review_data(items, handoffs=handoffs)


def _handoff(item_key, to, when):
    return {item_key: {"from_assignee": "", "to_assignee": to, "handed_at": when}}


def test_previous_assignee_is_credited_and_the_new_one_is_not():
    items = [_ritem("k_a", "Acme", "1040 Return", "Alice"),
             _ritem("k_b", "Acme", "1040 Return", "Bob")]
    data = _rdata(items, handoffs=_handoff("k_a", "Bob", "2026-06-29"))
    rv = review_mod.build_review(data, "NLC Financial",
                                 datetime(2026, 6, 29, 7, 0),
                                 last_import_date=REVIEW_TODAY)

    rows = {r["assignee"]: r for r in rv["staff_rows"]}
    assert rows["Alice"]["completed_week"] == 1
    assert rows["Alice"]["handoff_week"] == 1
    assert rows["Bob"]["completed_week"] == 0
    assert rows["Bob"]["open"] == 1          # Bob still holds the live document
    assert rows["Alice"]["open"] == 0        # ...and it left Alice's plate


def test_handoff_appears_in_the_firm_list_labelled():
    items = [_ritem("k_a", "Acme", "1040 Return", "Alice"),
             _ritem("k_b", "Acme", "1040 Return", "Bob")]
    data = _rdata(items, handoffs=_handoff("k_a", "Bob", "2026-06-29"))
    rv = review_mod.build_review(data, "NLC Financial",
                                 datetime(2026, 6, 29, 7, 0),
                                 last_import_date=REVIEW_TODAY)

    assert len(rv["completed_this_week"]) == 1
    row = rv["completed_this_week"][0]
    assert row["kind"] == "handoff"
    assert row["handed_to"] == "Bob"
    assert row["assignee"] == "Alice"
    assert row["completed_at"] == "2026-06-29"


def test_staff_credits_still_sum_to_the_firm_total():
    items = [_ritem("k_a", "Acme", "1040 Return", "Alice"),
             _ritem("k_b", "Acme", "1040 Return", "Bob"),
             _ritem("k_c", "Beta", "1120 Return", "Carol", status="Completed",
                    completed_date=date(2026, 6, 28))]
    data = _rdata(items, handoffs=_handoff("k_a", "Bob", "2026-06-29"))
    rv = review_mod.build_review(data, "NLC Financial",
                                 datetime(2026, 6, 29, 7, 0),
                                 last_import_date=REVIEW_TODAY)

    assert len(rv["completed_this_week"]) == 2       # one real, one handoff
    assert sum(r["completed_week"] for r in rv["staff_rows"]) == 2
    kinds = {r["assignee"]: r["kind"] for r in rv["completed_this_week"]}
    assert kinds == {"Alice": "handoff", "Carol": "completed"}


def test_handoff_outside_the_window_is_not_counted():
    items = [_ritem("k_a", "Acme", "1040 Return", "Alice"),
             _ritem("k_b", "Acme", "1040 Return", "Bob")]
    # 8 days before the import date -- the window is the trailing 7.
    data = _rdata(items, handoffs=_handoff("k_a", "Bob", "2026-06-21"))
    rv = review_mod.build_review(data, "NLC Financial",
                                 datetime(2026, 6, 29, 7, 0),
                                 last_import_date=REVIEW_TODAY)
    assert rv["completed_this_week"] == []


def test_out_of_scope_owner_handoff_is_not_counted():
    # Marcus Lorne-owned clients are outside the review's scope (_in_scope).
    items = [_ritem("k_a", "Acme", "1040 Return", "Alice"),
             _ritem("k_b", "Acme", "1040 Return", "Bob")]
    for it in items:
        it["client_owner"] = "Marcus Lorne"
    data = _rdata(items, handoffs=_handoff("k_a", "Bob", "2026-06-29"))
    rv = review_mod.build_review(data, "NLC Financial",
                                 datetime(2026, 6, 29, 7, 0),
                                 last_import_date=REVIEW_TODAY)
    assert rv["completed_this_week"] == []


def test_staff_page_reports_the_handoff_split():
    items = [_ritem("k_a", "Acme", "1040 Return", "Alice"),
             _ritem("k_b", "Acme", "1040 Return", "Bob")]
    data = _rdata(items, handoffs=_handoff("k_a", "Bob", "2026-06-29"))
    sp = review_mod.build_staff_page(data, "NLC Financial",
                                     datetime(2026, 6, 29, 7, 0), "Alice",
                                     last_import_date=REVIEW_TODAY)
    assert sp["found"] is True
    assert sp["completed_week"] == 1
    assert sp["handoff_week"] == 1
    assert sp["open"] == 0


def test_backfill_credits_the_import_that_followed_the_stale_row(tmp_path):
    """Handoffs that predate detection are dated the NEXT import after the
    stale row's last_seen -- the day the change actually showed up."""
    s = Store(tmp_path / "h.db")
    try:
        s.upsert_items([_rec("k_a", "Acme", "1040 Return", "Alice")], DAY1)
        s.record_import("day1.xlsx", 1, 1, DAY1)
        s.upsert_items([_rec("k_b", "Acme", "1040 Return", "Bob")], DAY2)
        s.record_import("day2.xlsx", 1, 1, DAY2)

        assert s.backfill_handoffs() == 1
        assert s.get_handoffs()["k_a"]["handed_at"] == "2026-06-30"
    finally:
        s.close()


def test_backfill_runs_only_once(tmp_path):
    s = Store(tmp_path / "h.db")
    try:
        s.upsert_items([_rec("k_a", "Acme", "1040 Return", "Alice")], DAY1)
        s.record_import("day1.xlsx", 1, 1, DAY1)
        s.upsert_items([_rec("k_b", "Acme", "1040 Return", "Bob")], DAY2)
        s.record_import("day2.xlsx", 1, 1, DAY2)

        assert s.backfill_handoffs() == 1
        assert s.backfill_handoffs() == 0
        assert len(s.get_handoffs()) == 1
    finally:
        s.close()


def test_backfill_with_no_import_history_is_a_no_op(tmp_path):
    s = Store(tmp_path / "h.db")
    try:
        assert s.backfill_handoffs() == 0
        assert s.get_setting("handoffs_backfilled") is True
    finally:
        s.close()
