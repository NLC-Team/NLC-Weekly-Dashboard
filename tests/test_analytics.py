from datetime import date

from data import analytics

TODAY = date(2026, 6, 29)


def _item(assignee, days_old, status="Pending", source=True):
    start = date(2026, 6, 29) - __import__("datetime").timedelta(days=days_old)
    return {
        "assignee": assignee,
        "client": "C",
        "title": f"{assignee}-{days_old}",
        "status": status,
        "first_seen": start,
        "source_date": start if source else None,
    }


def test_age_prefers_source_date():
    it = _item("A", 10)
    assert analytics.item_age_days(it, TODAY) == 10


def test_age_falls_back_to_first_seen():
    it = _item("A", 10, source=False)
    assert analytics.item_age_days(it, TODAY) == 10


def test_age_never_negative():
    it = {"assignee": "A", "title": "x", "first_seen": date(2026, 7, 10), "source_date": None}
    assert analytics.item_age_days(it, TODAY) == 0


def test_overdue_threshold_boundary():
    items = analytics.enrich_items([_item("A", 14), _item("B", 15)], TODAY, overdue_days=14)
    by_name = {it["assignee"]: it for it in items}
    assert by_name["A"]["overdue"] is False  # exactly at threshold = not overdue
    assert by_name["B"]["overdue"] is True


def test_totals():
    items = analytics.enrich_items(
        [_item("A", 5), _item("A", 20), _item("B", 30)], TODAY, overdue_days=14
    )
    t = analytics.totals(items)
    assert t["total_pending"] == 3
    assert t["total_overdue"] == 2
    assert t["staff_count"] == 2


def test_per_assignee_sorted_by_workload():
    items = analytics.enrich_items(
        [_item("A", 5), _item("A", 8), _item("B", 40)], TODAY, overdue_days=14
    )
    rows = analytics.per_assignee(items)
    assert rows[0]["assignee"] == "A"  # 2 items beats 1
    assert rows[0]["pending_count"] == 2
    assert rows[1]["assignee"] == "B"
    assert rows[1]["overdue_count"] == 1


def test_overdue_items_sorted_oldest_first():
    items = analytics.enrich_items([_item("A", 20), _item("B", 40)], TODAY, overdue_days=14)
    od = analytics.overdue_items(items)
    assert [it["age_days"] for it in od] == [40, 20]


def test_age_distribution_buckets():
    items = analytics.enrich_items(
        [_item("A", 1), _item("A", 10), _item("B", 100)], TODAY, overdue_days=14
    )
    dist = dict(analytics.age_distribution(items))
    assert dist["0-3d"] == 1
    assert dist["8-14d"] == 1
    assert dist["61d+"] == 1
