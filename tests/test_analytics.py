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


def test_status_is_done_matches_only_exact_completed():
    # "Done" means the status is EXACTLY "Completed" (case-insensitive, trimmed) --
    # a "Completed - *" variant does NOT count as done.
    assert analytics.status_is_done("Completed") is True
    assert analytics.status_is_done("completed") is True
    assert analytics.status_is_done("  Completed  ") is True
    assert analytics.status_is_done("Completed - Cancelled") is False
    assert analytics.status_is_done("Completed - Not a fit") is False
    assert analytics.status_is_done("Completed - Billed") is False


def test_status_is_done_rejects_open_statuses():
    for s in ("In Progress", "Ready To Start", "Waiting - Awaiting docs",
              "Planned - ON HOLD", "In Progress - Return Draft Approved", "", None):
        assert analytics.status_is_done(s) is False


def test_status_is_closed_other_matches_completed_variants():
    # "Closed (other)" = contains the word "completed" but is NOT exactly "Completed".
    assert analytics.status_is_closed_other("Completed - Cancelled") is True
    assert analytics.status_is_closed_other("Completed - Not a fit") is True
    assert analytics.status_is_closed_other("completed - billed") is True
    # The exact "Completed" is done, not "other"; open statuses are neither.
    assert analytics.status_is_closed_other("Completed") is False
    assert analytics.status_is_closed_other("In Progress") is False
    assert analytics.status_is_closed_other("") is False
    assert analytics.status_is_closed_other(None) is False


def test_overdue_threshold_boundary():
    items = analytics.enrich_items([_item("A", 14), _item("B", 15)], TODAY, overdue_days=14)
    by_name = {it["assignee"]: it for it in items}
    assert by_name["A"]["overdue"] is False  # exactly at threshold = not overdue
    assert by_name["B"]["overdue"] is True


def _item_due(assignee, due, status="Pending"):
    it = _item(assignee, days_old=5, status=status)
    it["due_date"] = due
    return it


def test_overdue_judged_from_due_date_when_present():
    # A due date overrides the age-based rule entirely, even for a "young" item
    # that the age threshold alone would call not-yet-overdue.
    items = analytics.enrich_items([
        _item_due("Future", TODAY + __import__("datetime").timedelta(days=5)),
        _item_due("Past", TODAY - __import__("datetime").timedelta(days=3)),
    ], TODAY, overdue_days=14)
    by_name = {it["assignee"]: it for it in items}
    assert by_name["Future"]["overdue"] is False
    assert by_name["Past"]["overdue"] is True
    assert by_name["Past"]["days_overdue"] == 3


def test_overdue_flips_the_day_the_due_date_hits():
    # "When the due date hits" -- today == due_date is already overdue (day 0),
    # not the day after.
    items = analytics.enrich_items([_item_due("A", TODAY)], TODAY, overdue_days=14)
    assert items[0]["overdue"] is True
    assert items[0]["days_overdue"] == 0
    # The day before the due date: not yet overdue.
    items2 = analytics.enrich_items(
        [_item_due("A", TODAY + __import__("datetime").timedelta(days=1))], TODAY, overdue_days=14)
    assert items2[0]["overdue"] is False


def test_overdue_falls_back_to_age_rule_when_due_date_blank():
    # No due date on the document -> the existing start-date + overdue_days
    # threshold still applies, so nothing regresses for undated rows.
    items = analytics.enrich_items([_item("A", 15)], TODAY, overdue_days=14)
    assert items[0].get("due_date") is None
    assert items[0]["overdue"] is True
    assert items[0]["days_overdue"] == 1


def _proj_item(key, client, title, days_old, status="In Progress"):
    start = TODAY - __import__("datetime").timedelta(days=days_old)
    return {"item_key": key, "assignee": "A", "client": client, "title": title,
            "status": status, "source_date": start, "first_seen": start,
            "project_key": "c:" + client.lower(), "return_type_raw": ""}


def test_project_days_overdue_uses_worst_document():
    # A project's days_overdue mirrors days_open: driven by its WORST (oldest
    # overdue) document, since that document is what put the whole client into
    # Overdue in the first place, however fresh its other documents are.
    items = analytics.enrich_items([
        _proj_item("a", "Mixed", "Old Doc", 40),   # 40-14 = 26 days overdue
        _proj_item("b", "Mixed", "New Doc", 16),   # 16-14 = 2 days overdue
    ], TODAY, overdue_days=14)
    projects = analytics.build_projects(items, {}, {})
    p = projects[0]
    assert p["overdue"] is True
    assert p["days_overdue"] == 26


def test_project_days_overdue_zero_when_not_overdue():
    items = analytics.enrich_items([_proj_item("a", "Fresh", "Doc", 5)],
                                   TODAY, overdue_days=14)
    p = analytics.build_projects(items, {}, {})[0]
    assert p["overdue"] is False
    assert p["days_overdue"] == 0


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
