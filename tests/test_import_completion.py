from datetime import date

from data.store import Store

TODAY = date(2026, 6, 29)
NEXT = date(2026, 7, 6)
DONE = ["Completed"]


def _rec(key, client, title, status):
    return {"item_key": key, "assignee": "A", "client": client, "title": title,
            "status": status, "source_date": None,
            "project_key": "c:" + client.lower(), "return_type_raw": ""}


def test_all_docs_completed_autocompletes(tmp_path):
    s = Store(tmp_path / "a.db")
    try:
        r = s.upsert_items([_rec("1", "Smith", "W-2", "Completed"),
                            _rec("2", "Smith", "1099", "Completed")], TODAY)
        res = s.apply_import_completion(DONE, r["new_keys"], TODAY)
        assert res["completed"] == 1
        st = s.get_project_states()["c:smith"]
        assert st["completed"] and st["completed_source"] == "import"
        assert st["completed_at"] == TODAY.isoformat()
    finally:
        s.close()


def test_partial_not_completed(tmp_path):
    s = Store(tmp_path / "b.db")
    try:
        r = s.upsert_items([_rec("1", "Smith", "W-2", "Completed"),
                            _rec("2", "Smith", "1099", "In Progress")], TODAY)
        res = s.apply_import_completion(DONE, r["new_keys"], TODAY)
        assert res["completed"] == 0
        assert not s.get_project_states().get("c:smith", {}).get("completed")
    finally:
        s.close()


def test_import_completed_reopens_when_new_active(tmp_path):
    s = Store(tmp_path / "c.db")
    try:
        r = s.upsert_items([_rec("1", "Smith", "W-2", "Completed")], TODAY)
        s.apply_import_completion(DONE, r["new_keys"], TODAY)
        assert s.get_project_states()["c:smith"]["completed"]
        r2 = s.upsert_items([_rec("1", "Smith", "W-2", "Completed"),
                             _rec("2", "Smith", "1099", "In Progress")], NEXT)
        res = s.apply_import_completion(DONE, r2["new_keys"], NEXT)
        assert res["reopened"] == 1
        assert not s.get_project_states()["c:smith"]["completed"]
    finally:
        s.close()


def test_manual_completion_reopens_on_new_active_statements(tmp_path):
    # The clarified rule: a hand-completed client that gets NEW active statements
    # in a later import is reopened (taken out of the Completed tab).
    s = Store(tmp_path / "d.db")
    try:
        s.upsert_items([_rec("1", "Smith", "W-2", "In Progress")], TODAY)
        s.set_project_completed("c:smith", True, TODAY, source="manual")
        r = s.upsert_items([_rec("1", "Smith", "W-2", "In Progress"),
                            _rec("2", "Smith", "1099", "In Progress")], NEXT)
        res = s.apply_import_completion(DONE, r["new_keys"], NEXT)
        assert res["reopened"] == 1
        assert not s.get_project_states()["c:smith"]["completed"]
    finally:
        s.close()


def test_manual_completion_sticky_without_new_statements(tmp_path):
    # No new statements added -> a hand completion is left alone.
    s = Store(tmp_path / "e.db")
    try:
        s.upsert_items([_rec("1", "Smith", "W-2", "In Progress")], TODAY)
        s.set_project_completed("c:smith", True, TODAY, source="manual")
        r = s.upsert_items([_rec("1", "Smith", "W-2", "In Progress")], NEXT)  # same doc
        res = s.apply_import_completion(DONE, r["new_keys"], NEXT)
        assert res["reopened"] == 0
        assert s.get_project_states()["c:smith"]["completed"]
    finally:
        s.close()


def test_no_completed_statuses_is_noop(tmp_path):
    s = Store(tmp_path / "f.db")
    try:
        r = s.upsert_items([_rec("1", "Smith", "W-2", "Completed")], TODAY)
        res = s.apply_import_completion([], r["new_keys"], TODAY)
        assert res == {"completed": 0, "reopened": 0}
        assert not s.get_project_states().get("c:smith", {}).get("completed")
    finally:
        s.close()
