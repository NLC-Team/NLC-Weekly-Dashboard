from datetime import date

import pytest

from data.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


def _rec(key, assignee="Sarah", status="Pending", source_date=None, title="T", client="C"):
    return {
        "item_key": key,
        "assignee": assignee,
        "client": client,
        "title": title,
        "status": status,
        "source_date": source_date,
    }


def test_settings_roundtrip(store):
    store.set_setting("overdue_days", 21)
    assert store.get_setting("overdue_days") == 21
    store.set_setting("pending", ["A", "B"])
    assert store.get_setting("pending") == ["A", "B"]
    assert store.get_setting("missing", "default") == "default"


def test_mapping_roundtrip(store):
    store.save_mapping("sig1", {"assignee": "Owner"}, date(2026, 6, 29))
    assert store.get_mapping("sig1") == {"assignee": "Owner"}
    assert store.get_mapping("nope") is None


def test_first_seen_is_stable_across_imports(store):
    day1 = date(2026, 6, 1)
    day2 = date(2026, 6, 10)
    store.upsert_items([_rec("id:1")], day1)
    store.upsert_items([_rec("id:1")], day2)  # same item, later import
    items = store.active_items()
    assert len(items) == 1
    assert items[0]["first_seen"] == day1  # unchanged


def test_source_date_used_as_first_seen(store):
    src = date(2026, 5, 20)
    store.upsert_items([_rec("id:1", source_date=src)], date(2026, 6, 1))
    items = store.active_items()
    assert items[0]["first_seen"] == src


def test_imports_are_additive_and_keep_vanished_items(store):
    """A later, partial import must NOT wipe items it doesn't contain."""
    day1 = date(2026, 6, 1)
    day2 = date(2026, 6, 10)
    store.upsert_items([_rec("id:1"), _rec("id:2")], day1)
    stats = store.upsert_items([_rec("id:1")], day2)  # id:2 absent this time
    assert "resolved" not in stats            # nothing is auto-removed anymore
    keys = {it["item_key"] for it in store.active_items()}
    assert keys == {"id:1", "id:2"}           # both kept


def test_import_accumulates_across_files(store):
    """Importing a different file adds to the data rather than replacing it."""
    store.upsert_items([_rec("id:1")], date(2026, 6, 1))
    store.upsert_items([_rec("id:2")], date(2026, 6, 10))  # a different export
    keys = {it["item_key"] for it in store.active_items()}
    assert keys == {"id:1", "id:2"}


def test_import_stats(store):
    stats = store.upsert_items([_rec("id:1"), _rec("id:2")], date(2026, 6, 1))
    assert stats == {"new": 2, "updated": 0}
    store.record_import("file.csv", 10, 2, date(2026, 6, 1))
    assert store.last_import()["pending_count"] == 2
