"""Tests for review.build_staff_workbook — the per-staff Excel worklists.

Distinct from the rest of the Weekly Review in three ways that all need guarding:
it is DOCUMENT grain (nothing merged), it carries only OPEN work (completed and
closed-out rows are not "due"), and it groups by each document's OWN Work Type
rather than the project-merged type. Scope is the review's usual _in_scope.

Reuses test_review's `_item`/`_data` fixtures — `_data` mirrors
service.dashboard_data, and a second copy of that mirror would silently drift.
"""
from datetime import datetime, timedelta

import pytest

import config

from data import review
from test_review import OWNER_PREFIX, TODAY, _data, _item


@pytest.fixture(autouse=True)
def review_owner_scope(monkeypatch):
    """Same owner scope as test_review, whose helpers these tests reuse."""
    monkeypatch.setattr(config, "REVIEW_OWNER_PREFIX", OWNER_PREFIX)


def _witem(key, client, title, days, due_in=None, **kw):
    """test_review._item plus a due date, expressed as days from TODAY (negative =
    already past due). `_item` alone leaves due_date unset, which sends
    enrich_items down its age-based fallback."""
    it = _item(key, client, title, days, **kw)
    if due_in is not None:
        it["due_date"] = TODAY + timedelta(days=due_in)
    return it


def _book(items, **kw):
    return review.build_staff_workbook(_data(items, **kw), "NLC Financial",
                                       datetime(2026, 6, 29, 7, 0))


def _sheet(book, assignee):
    return next(s for s in book["sheets"] if s["assignee"] == assignee)


def _rows(book, assignee):
    """Every row on one person's sheet, in sheet order (blocks then rows)."""
    return [r for g in _sheet(book, assignee)["groups"] for r in g["rows"]]


def test_one_row_per_document_never_merged_by_client():
    # Two documents for the same client AND the same work type stay two rows --
    # the engagement-level views merge them, this one must not.
    book = _book([
        _witem("a", "Acme", "1040 Return", 40, due_in=-30),
        _witem("b", "Acme", "State Return", 40, due_in=-30),
    ])
    rows = _rows(book, "Sarah")
    assert [r["title"] for r in rows] == ["1040 Return", "State Return"]
    assert _sheet(book, "Sarah")["open_count"] == 2


def test_completed_and_closed_other_rows_are_excluded():
    book = _book([
        _witem("open", "Acme", "W-2", 40, due_in=-30),
        _witem("done", "Beta", "1099", 40, due_in=-30, status="Completed"),
        _witem("cancelled", "Ceta", "K-1", 40, due_in=-30,
               status="Completed - Cancelled"),
        _witem("billed", "Delta", "990", 40, due_in=-30, status="Completed - Billed"),
    ])
    assert [r["client"] for r in _rows(book, "Sarah")] == ["Acme"]


def test_out_of_scope_rows_are_excluded():
    book = _book([
        _witem("mine", "Acme", "W-2", 40, due_in=-30),
        _witem("other-owner", "Beta", "1099", 40, due_in=-30, owner="Marcus Lorne"),
        _witem("no-corr", "Ceta", "K-1", 40, due_in=-30,
               status="Ready To Start - NO CORRESPONDENCE"),
        _witem("bot", "Delta", "990", 40, due_in=-30, assignee="Karbon Support"),
    ])
    assert [s["assignee"] for s in book["sheets"]] == ["Sarah"]
    assert [r["client"] for r in _rows(book, "Sarah")] == ["Acme"]


def test_blank_owner_clients_are_included():
    # The firm rule: a blank Client Owner is "not excluded", not "dropped".
    book = _book([_witem("a", "Acme", "W-2", 40, due_in=-30, owner="")])
    assert [r["client"] for r in _rows(book, "Sarah")] == ["Acme"]


def test_rows_inside_a_work_type_block_run_most_overdue_first():
    book = _book([
        _witem("a", "Acme", "W-2", 40, due_in=-5),
        _witem("b", "Beta", "W-2", 40, due_in=-90),
        _witem("c", "Ceta", "W-2", 40, due_in=-40),
    ])
    rows = _rows(book, "Sarah")
    assert [r["client"] for r in rows] == ["Beta", "Ceta", "Acme"]
    assert [r["days_overdue"] for r in rows] == [90, 40, 5]


def test_not_yet_due_rows_sit_below_the_overdue_ones():
    book = _book([
        _witem("soon", "Acme", "W-2", 3, due_in=30),      # not due yet
        _witem("late", "Beta", "1099", 40, due_in=-20),    # 20 overdue
    ])
    rows = _rows(book, "Sarah")
    assert [(r["client"], r["days_overdue"]) for r in rows] == [("Beta", 20), ("Acme", 0)]


def test_work_type_blocks_ordered_by_their_worst_overdue():
    book = _book([
        _witem("p", "Acme", "Q1 Payroll", 40, due_in=-10, rtype_raw="Payroll"),
        _witem("t", "Beta", "1040 Return", 40, due_in=-60, rtype_raw="Tax: 1040"),
        _witem("b", "Ceta", "Jan books", 40, due_in=-30, rtype_raw="Bookkeeping"),
    ])
    groups = _sheet(book, "Sarah")["groups"]
    assert [g["work_type"] for g in groups] == ["Tax: 1040", "Bookkeeping", "Payroll"]
    assert [g["max_days_overdue"] for g in groups] == [60, 30, 10]


def test_work_type_is_a_literal_copy_of_the_documents_own_work_type():
    # A client with two work types has ONE client-level project key (the export has
    # no Project column), so build_projects gives the WHOLE CLIENT a single
    # effective return_type taken from its first non-blank document. Reporting that
    # would file the Payroll row under the tax type; every row must carry a verbatim
    # copy of its own Work Type cell instead.
    book = _book([
        _witem("t", "Acme", "1040 Return", 40, due_in=-60, rtype_raw="Tax: 1040"),
        _witem("p", "Acme", "Q1 Payroll", 40, due_in=-30, rtype_raw="Payroll"),
    ])
    groups = {g["work_type"]: g for g in _sheet(book, "Sarah")["groups"]}
    assert set(groups) == {"Tax: 1040", "Payroll"}
    assert [r["title"] for r in groups["Payroll"]["rows"]] == ["Q1 Payroll"]
    # The merged client-level type collapsed both documents to one value; had we
    # reported it, one of these two rows would have been mislabelled.
    assert len({it["return_type"] for it in _data([
        _witem("t", "Acme", "1040 Return", 40, due_in=-60, rtype_raw="Tax: 1040"),
        _witem("p", "Acme", "Q1 Payroll", 40, due_in=-30, rtype_raw="Payroll"),
    ])["items"]}) == 1


def test_no_project_type_column_is_reported():
    # Dropped deliberately (user: "no need to confuse things with a project type") --
    # on real data it contradicted the document's own Work Type on ~64% of rows.
    book = _book([_witem("a", "Acme", "1040 Return", 40, due_in=-30)])
    assert "project_type" not in _rows(book, "Sarah")[0]


def test_blank_work_type_reads_unclassified():
    book = _book([_witem("a", "Acme", "Swag Order", 40, due_in=-30, rtype_raw="")])
    assert _rows(book, "Sarah")[0]["work_type"] == "Unclassified"


def test_work_type_is_only_trimmed_never_remapped():
    # "Accounting/Bookeeping" (the export's own spelling, typo included) must come
    # through verbatim -- it is a copy-paste of the cell, not a normalized category.
    book = _book([_witem("a", "Acme", "Jan books", 40, due_in=-30,
                         rtype_raw="  Accounting/Bookeeping  ")])
    assert _rows(book, "Sarah")[0]["work_type"] == "Accounting/Bookeeping"


def test_blank_due_date_falls_back_to_the_age_rule_and_leaves_the_cell_empty():
    book = _book([_item("a", "Acme", "W-2", 40)])       # no due_date at all
    row = _rows(book, "Sarah")[0]
    assert row["due_date"] is None
    assert row["days_overdue"] == 26        # 40 days old - 14-day overdue threshold
    assert row["days_open"] == 40


def test_row_carries_client_title_status_dates_and_assignee():
    book = _book([_witem("a", "Acme", "1040 Return", 40, due_in=-30,
                         status="In Progress", rtype_raw="Tax: 1040")])
    row = _rows(book, "Sarah")[0]
    assert row["client"] == "Acme"
    assert row["title"] == "1040 Return"
    assert row["status"] == "In Progress"
    assert row["work_type"] == "Tax: 1040"
    assert row["start_date"] == TODAY - timedelta(days=40)
    assert row["due_date"] == TODAY - timedelta(days=30)
    assert row["assignee"] == "Sarah"


def test_sheets_ordered_by_overdue_count_with_unassigned_last():
    book = _book([
        _witem("u", "Acme", "W-2", 40, due_in=-90, assignee=""),          # Unassigned
        _witem("j1", "Beta", "1099", 40, due_in=-10, assignee="James"),
        _witem("s1", "Ceta", "K-1", 40, due_in=-10, assignee="Sarah"),
        _witem("s2", "Delta", "990", 40, due_in=-10, assignee="Sarah"),
    ])
    # Sarah has 2 overdue, James 1; Unassigned goes last despite the worst row.
    assert [s["assignee"] for s in book["sheets"]] == ["Sarah", "James", "Unassigned"]


def test_open_and_overdue_counts_are_per_sheet():
    book = _book([
        _witem("late", "Acme", "W-2", 40, due_in=-30),
        _witem("soon", "Beta", "1099", 3, due_in=30),
    ])
    s = _sheet(book, "Sarah")
    assert (s["open_count"], s["overdue_count"]) == (2, 1)


def test_staff_with_only_completed_work_gets_no_sheet():
    # Nothing is due for them, so there is no worklist to hand over.
    book = _book([
        _witem("a", "Acme", "W-2", 40, due_in=-30, assignee="Sarah"),
        _witem("b", "Beta", "1099", 40, due_in=-30, assignee="James",
               status="Completed"),
    ])
    assert [s["assignee"] for s in book["sheets"]] == ["Sarah"]


def test_a_sheet_only_lists_documents_that_person_actually_owns():
    # A sheet must contain only documents this person currently owns AND that are
    # still open -- never a document that belongs to a different assignee just
    # because it shares a client/work-type with something this person does own.
    # James's document here is completed (closed), so it must not appear on his
    # sheet, and he must not get a sheet at all just because Sarah's sibling
    # document under the same client/work-type is still open. Both the Excel and
    # the Weekly Review page now count at DOCUMENT grain, so a person's sheet row
    # count matches their own "Open" tile on the Weekly Review page exactly.
    book = _book([
        _witem("james-part", "Acme", "Bookkeeping Jan", 40, due_in=-30,
               assignee="James", rtype_raw="Bookkeeping", status="Completed"),
        _witem("sarah-part", "Acme", "Bookkeeping Feb", 40, due_in=-30,
               assignee="Sarah", rtype_raw="Bookkeeping"),
    ])
    assert [s["assignee"] for s in book["sheets"]] == ["Sarah"]
    assert [r["title"] for r in _rows(book, "Sarah")] == ["Bookkeeping Feb"]


def test_empty_data_yields_no_sheets():
    book = _book([])
    assert book["sheets"] == []
    assert book["firm_name"] == "NLC Financial"
