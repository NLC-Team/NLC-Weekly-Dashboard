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
