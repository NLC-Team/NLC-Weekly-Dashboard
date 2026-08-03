from datetime import date, datetime, timedelta

from data import analytics, review

TODAY = date(2026, 6, 29)


def _item(key, client, title, days, assignee="Sarah", rtype_raw="1040 Individual",
          owner="Owen Bradfield", status="Requested", completed_date=None):
    start = TODAY - timedelta(days=days)
    return {
        "item_key": key, "client": client, "title": title, "status": status,
        "first_seen": start, "source_date": start, "return_type_raw": rtype_raw,
        "assignee": assignee, "project_key": "c:" + client.lower(),
        "client_owner": owner, "completed_date": completed_date,
    }


def _data(items, doc_states=None, project_states=None, overdue_days=14):
    """Mirror service.dashboard_data: enrich, classify each document as
    completed/closed_other, fold closed work out of overdue, set each item's
    effective return_type, and expose data['items'] + data['overdue'].

    data['items'] is the FULL row list (closed ones included) — the Weekly
    Review's document-level "Completed this week" section reads it.
    """
    doc_states = doc_states or {}
    project_states = project_states or {}
    enriched = analytics.enrich_items(items, TODAY, overdue_days)
    completed = {k for k, v in project_states.items() if v.get("completed")}
    manual = {k for k, v in project_states.items()
              if v.get("completed") and v.get("completed_source") == "manual"}
    for it in enriched:
        it["received"] = bool(doc_states.get(it["item_key"]))
        pkey = it.get("project_key")
        own_closed_other = analytics.status_is_closed_other(it.get("status"))
        it["completed"] = (
            analytics.status_is_done(it.get("status"))
            or it["received"]
            or pkey in manual
            or (pkey in completed and not own_closed_other)
        )
        it["closed_other"] = own_closed_other and not it["completed"]
        it["closed"] = it["completed"] or it["closed_other"]
        if it["closed"]:
            it["overdue"] = False
            it["days_overdue"] = 0
    projects = analytics.build_projects(enriched, doc_states, project_states)
    type_by_pkey = {p["project_key"]: p["return_type"] for p in projects}
    for it in enriched:
        pkey = it.get("project_key") or ("c:" + it["client"].strip().lower())
        it["return_type"] = type_by_pkey.get(pkey)
    return {"items": enriched, "projects": projects,
            "overdue": analytics.overdue_items(enriched)}


def _build(items, **kw):
    return review.build_review(_data(items, **kw), "NLC Financial",
                               datetime(2026, 6, 29, 7, 0), top_n=10)


def test_top_ranks_statements_by_days_overdue():
    r = _build([
        _item("a", "Acme", "W-2", 40),      # 26 overdue
        _item("b", "Beta", "1099", 60),     # 46 overdue
        _item("c", "Ceta", "K-1", 5),       # not overdue -> excluded
    ])
    assert [t["client"] for t in r["top"]] == ["Beta", "Acme"]
    assert r["top"][0]["rank"] == 1
    assert r["top"][0]["days_overdue"] == 46   # 60 - 14
    assert r["top"][0]["days_open"] == 60
    assert r["total_overdue"] == 2


def test_top_lists_each_statement_even_same_client():
    # A client with two overdue documents appears twice (statement-level).
    r = _build([
        _item("a1", "Acme", "W-2", 50),
        _item("a2", "Acme", "1099", 40),
    ])
    assert [(t["client"], t["title"]) for t in r["top"]] == [("Acme", "W-2"), ("Acme", "1099")]


def test_top_capped_at_top_n():
    items = [_item(str(i), f"Client{i:02d}", "Doc", 20 + i) for i in range(15)]
    r = _build(items)
    assert len(r["top"]) == 10
    assert r["top"][0]["days_overdue"] == 34 - 14  # oldest (20+14 days) first


def test_completed_and_received_statements_excluded():
    r = _build(
        [_item("a", "Acme", "W-2", 40), _item("b", "Beta", "1099", 30),
         _item("c", "Ceta", "K-1", 50)],
        doc_states={"b": True},                                   # received -> out
        project_states={"c:ceta": {"return_type": "x", "completed": True}},  # completed -> out
    )
    assert [t["client"] for t in r["top"]] == ["Acme"]
    assert r["total_overdue"] == 1


def test_per_staff_type_counts():
    r = _build([
        _item("a", "Acme", "W-2", 40, assignee="Sarah", rtype_raw="Tax: 1040"),
        _item("b", "Beta", "1099", 30, assignee="Sarah", rtype_raw="Tax: 1040"),
        _item("c", "Ceta", "TB", 30, assignee="Sarah", rtype_raw="Tax: 1120"),
        _item("d", "Delt", "K-1", 30, assignee="James", rtype_raw="Tax: 1065"),
    ])
    staff = {s["assignee"]: s for s in r["per_staff"]}
    assert staff["Sarah"]["count"] == 3
    # Most common type first: 1040 (x2) before 1120 (x1).
    assert staff["Sarah"]["types"][0] == {"type": "Tax: 1040", "count": 2}
    assert staff["Sarah"]["types"][1] == {"type": "Tax: 1120", "count": 1}
    # Busiest staff leads the section.
    assert r["per_staff"][0]["assignee"] == "Sarah"
    assert staff["James"]["count"] == 1


IMPORT_DAY = date(2026, 6, 29)          # a Monday
WINDOW_FIRST_DAY = IMPORT_DAY - timedelta(days=6)   # trailing 7 days, inclusive


def _done(key, client, title, completed_on, **kw):
    """A document Karbon reports as completed on `completed_on`."""
    kw.setdefault("status", "Completed")
    return _item(key, client, title, 40, completed_date=completed_on, **kw)


def test_completed_this_week_window_and_scope():
    # The window is the trailing 7 days ENDING on the import date (inclusive at
    # both ends), and stays inside the review's Owen-owned scope.
    gen = datetime(2026, 6, 29, 7, 0)
    items = [
        _done("a", "Acme", "W-2", IMPORT_DAY),
        _done("b", "Beta", "1099", WINDOW_FIRST_DAY),
        _done("c", "Ceta", "K-1", WINDOW_FIRST_DAY - timedelta(days=1)),
        _done("d", "Delt", "1065", IMPORT_DAY, owner="Marcus Lorne"),
        _item("e", "Echo", "W-9", 40),   # never completed -> no completed_date
    ]
    r = review.build_review(_data(items), "NLC", gen, last_import_date=IMPORT_DAY)
    clients = [c["client"] for c in r["completed_this_week"]]
    assert "Acme" in clients      # completed on the import day itself
    assert "Beta" in clients      # oldest day still inside the 7-day window
    assert "Ceta" not in clients  # one day past the window
    assert "Delt" not in clients  # inside the window but not Owen-owned
    assert "Echo" not in clients  # no completion date at all
    assert r["week_start"] == WINDOW_FIRST_DAY.isoformat()


def test_completed_this_week_counts_each_document_not_the_client():
    # The point of counting documents: two returns finished for ONE client are
    # TWO completions. The old project-level tally collapsed them into one,
    # because analytics.build_projects groups client-level when the export has
    # no project column.
    gen = datetime(2026, 6, 29, 7, 0)
    items = [
        _done("a1", "Acme", "1040 Return", IMPORT_DAY),
        _done("a2", "Acme", "State Return", IMPORT_DAY),
        _done("b1", "Beta", "1099", IMPORT_DAY),
    ]
    r = review.build_review(_data(items), "NLC", gen, last_import_date=IMPORT_DAY)
    done = r["completed_this_week"]
    assert len(done) == 3
    assert [c["client"] for c in done].count("Acme") == 2
    assert {c["title"] for c in done} == {"1040 Return", "State Return", "1099"}


def test_completed_window_anchors_to_import_date_not_calendar_week():
    # Work finished just before a calendar week flips must not silently fall out
    # of every report: the window follows the import date, whatever weekday that
    # is, rather than resetting on a fixed Sunday.
    gen = datetime(2026, 6, 29, 7, 0)      # viewed on a Monday
    last_import = date(2026, 6, 25)        # imported the previous Thursday
    items = [_done("a", "Gamma", "W-2", last_import)]
    data = _data(items)

    r = review.build_review(data, "NLC", gen, last_import_date=last_import)
    assert "Gamma" in [c["client"] for c in r["completed_this_week"]]
    assert r["week_start"] == (last_import - timedelta(days=6)).isoformat()


def test_staff_completed_week_totals_match_the_headline_list():
    # Every completion is credited to its own document's assignee, so summing the
    # per-staff tiles reproduces the headline count exactly.
    gen = datetime(2026, 6, 29, 7, 0)
    items = [
        _done("a1", "Acme", "1040 Return", IMPORT_DAY, assignee="Sarah"),
        _done("a2", "Acme", "State Return", IMPORT_DAY, assignee="James"),
        _done("b1", "Beta", "1099", IMPORT_DAY, assignee="Sarah"),
    ]
    r = review.build_review(_data(items), "NLC", gen, last_import_date=IMPORT_DAY)
    by_staff = {s["assignee"]: s for s in r["staff_rows"]}
    assert by_staff["Sarah"]["completed_week"] == 2
    assert by_staff["James"]["completed_week"] == 1
    assert (sum(s["completed_week"] for s in r["staff_rows"])
            == len(r["completed_this_week"]) == 3)


def test_completed_this_week_excludes_closed_other():
    # A document closed out ONLY via a "Completed - <word>" status (see
    # analytics.status_is_closed_other) is not a real completion -- Karbon still
    # stamps a Completed Date on it, so it must be filtered out explicitly.
    gen = datetime(2026, 6, 29, 7, 0)
    items = [
        _done("a", "ClosedOther", "Extension", IMPORT_DAY, status="Completed - Cancelled"),
        _done("b", "RealDone", "1040 Return", IMPORT_DAY),
    ]
    r = review.build_review(_data(items), "NLC", gen, last_import_date=IMPORT_DAY)
    clients = [c["client"] for c in r["completed_this_week"]]
    assert "ClosedOther" not in clients
    assert clients == ["RealDone"]


def test_staff_page_recent_overdue_projects_sorted_freshest_first():
    # "10 most recently overdue" = smallest days_overdue first (just crossed the
    # threshold), at DOCUMENT level -- same document set as top_overdue, just the
    # opposite sort order. Excludes non-overdue documents and other staff members'
    # work.
    items = [
        _item("a", "OldClient", "W-2", 60, assignee="Sarah"),      # long overdue
        _item("b", "FreshClient", "1099", 16, assignee="Sarah"),   # just overdue
        _item("c", "OpenNotOverdue", "K-1", 5, assignee="Sarah"),  # not overdue
        _item("d", "OtherStaff", "1040", 20, assignee="James"),    # different staff
    ]
    sp = review.build_staff_page(_data(items), "NLC", datetime(2026, 6, 29, 7, 0), "Sarah")
    rows = sp["recent_overdue"]
    assert [r["client"] for r in rows] == ["FreshClient", "OldClient"]
    assert rows[0]["days_overdue"] < rows[1]["days_overdue"]
    assert [r["rank"] for r in rows] == [1, 2]
    assert all(r["return_type"] for r in rows)
    assert rows[0]["title"] == "1099"
    assert rows[1]["title"] == "W-2"


def test_staff_page_recent_overdue_shows_each_document_separately():
    # Two overdue documents under the same client no longer collapse into one
    # joined-title row -- each document is its own row, matching top_overdue
    # and the Excel worklist (build_staff_workbook).
    items = [
        _item("a1", "Multi", "1040 Return", 40, assignee="Sarah"),
        _item("a2", "Multi", "State Return", 40, assignee="Sarah"),
    ]
    sp = review.build_staff_page(_data(items), "NLC", datetime(2026, 6, 29, 7, 0), "Sarah")
    assert len(sp["recent_overdue"]) == 2
    assert {r["title"] for r in sp["recent_overdue"]} == {"1040 Return", "State Return"}


def test_staff_page_lists_show_each_documents_own_work_type():
    # A staff page's per-document lists must show each document's OWN Work Type.
    # _stmt_row defaults return_type to the PROJECT-merged type, which collapses a
    # client's mixed work into one type (here both rows would read "Tax: 1040").
    items = [
        _item("a1", "Mixed", "1040 Return", 40, assignee="Sarah", rtype_raw="Tax: 1040"),
        _item("a2", "Mixed", "Payroll Q3", 40, assignee="Sarah", rtype_raw="Payroll"),
    ]
    sp = review.build_staff_page(_data(items), "NLC", datetime(2026, 6, 29, 7, 0), "Sarah")
    assert ({(r["title"], r["return_type"]) for r in sp["top_overdue"]}
            == {("1040 Return", "Tax: 1040"), ("Payroll Q3", "Payroll")})
    assert ({(r["title"], r["return_type"]) for r in sp["recent_overdue"]}
            == {("1040 Return", "Tax: 1040"), ("Payroll Q3", "Payroll")})


def test_staff_page_recent_overdue_capped_at_recent_n():
    items = [_item(str(i), f"Client{i:02d}", "Doc", 20 + i, assignee="Sarah")
             for i in range(15)]
    sp = review.build_staff_page(_data(items), "NLC", datetime(2026, 6, 29, 7, 0),
                                 "Sarah", recent_n=10)
    assert len(sp["recent_overdue"]) == 10
    assert sp["recent_overdue"][0]["days_overdue"] == 20 - 14  # freshest (20 days old) first


def test_unassigned_sorted_last():
    r = _build([
        _item("a", "Acme", "W-2", 40, assignee="(unassigned)"),
        _item("b", "Beta", "1099", 20, assignee="James"),
    ])
    assert r["per_staff"][-1]["assignee"] == "Unassigned"


def test_only_nuno_owned_and_blank_owner_clients_included():
    # A blank Client Owner (never set in Karbon) must not silently drop a client
    # out of the review -- only an explicitly DIFFERENT owner is excluded.
    r = _build([
        _item("a", "Acme", "W-2", 40, owner="Owen Bradfield"),
        _item("b", "Beta", "1099", 60, owner="Marcus Lorne"),      # other owner -> out
        _item("c", "Ceta", "K-1", 50, owner=None),               # blank owner -> included
    ])
    assert [t["client"] for t in r["top"]] == ["Ceta", "Acme"]
    assert r["total_overdue"] == 2


def test_no_correspondence_status_excluded_but_not_titles():
    r = _build([
        _item("a", "Acme", "W-2", 40, status="Ready To Start - NO CORRESPONDENCE"),  # out
        _item("b", "Beta", "IRS Correspondence - Refund Issue", 60,
              status="In Progress"),   # legit correspondence TITLE stays in
    ])
    assert [t["client"] for t in r["top"]] == ["Beta"]
    assert r["total_overdue"] == 1


def test_per_staff_has_statement_table_and_type_totals():
    r = _build([
        _item("a", "Acme", "W-2", 60, assignee="Sarah", rtype_raw="Tax: 1040"),
        _item("b", "Beta", "1099", 40, assignee="Sarah", rtype_raw="Tax: 1040"),
        _item("c", "Ceta", "TB", 30, assignee="Sarah", rtype_raw="Tax: 1120"),
    ])
    sarah = r["per_staff"][0]
    assert sarah["assignee"] == "Sarah"
    assert sarah["count"] == 3
    # Full statement list, worst-first, ranked within the staff member.
    assert [s["title"] for s in sarah["statements"]] == ["W-2", "1099", "TB"]
    assert [s["rank"] for s in sarah["statements"]] == [1, 2, 3]
    assert sarah["statements"][0]["days_overdue"] == 60 - 14
    # Type totals still present underneath.
    assert sarah["types"][0] == {"type": "Tax: 1040", "count": 2}


def test_per_staff_statements_capped_but_totals_complete():
    # 14 overdue for one staffer: only the worst 10 are listed, but count and the
    # type totals reflect all 14.
    items = [_item(f"k{i}", f"Client{i:02d}", "Doc", 20 + i, assignee="Sarah",
                   rtype_raw="Tax: 1040") for i in range(14)]
    r = _build(items)
    sarah = r["per_staff"][0]
    assert sarah["count"] == 14
    assert len(sarah["statements"]) == 10          # capped
    assert sarah["statements"][0]["rank"] == 1
    assert sarah["statements"][0]["days_overdue"] == (20 + 13) - 14  # worst first
    assert sum(t["count"] for t in sarah["types"]) == 14            # totals complete


def test_staff_rows_count_documents_not_engagements():
    # Two open documents of the same work type for one client must count as 2,
    # not collapse into 1 (client, work type) engagement.
    r = _build([
        _item("a1", "Acme", "1040 Return", 20, assignee="Sarah", rtype_raw="Tax: 1040"),
        _item("a2", "Acme", "State Return", 20, assignee="Sarah", rtype_raw="Tax: 1040"),
    ])
    sarah = next(s for s in r["staff_rows"] if s["assignee"] == "Sarah")
    assert sarah["open"] == 2
    assert sarah["overdue"] == 2
    assert sarah["overdue_by_type"] == [{"type": "Tax: 1040", "count": 2}]


def test_staff_rows_credit_only_current_assignee_not_past_touchers():
    # Sarah's own document in this (client, work type) pair is done; Bob's
    # document in the SAME pair is still open. Sarah must not be credited for
    # Bob's still-open work just because she once touched this pair.
    gen = datetime(2026, 6, 29, 7, 0)
    items = [
        _item("a1", "Acme", "1040 Return", 20, assignee="Sarah", rtype_raw="Tax: 1040",
              status="Completed"),
        _item("a2", "Acme", "State Return", 20, assignee="Bob", rtype_raw="Tax: 1040"),
    ]
    r = review.build_review(_data(items), "NLC", gen)
    by_staff = {s["assignee"]: s for s in r["staff_rows"]}
    assert "Sarah" not in by_staff          # her own piece is done -> drops off entirely
    assert by_staff["Bob"]["open"] == 1
    assert by_staff["Bob"]["overdue"] == 1


def test_staff_page_credit_only_current_assignee():
    # Same scenario as above, checked through build_staff_page's headline
    # tiles (what the PDF and on-screen detail page both render).
    gen = datetime(2026, 6, 29, 7, 0)
    items = [
        _item("a1", "Acme", "1040 Return", 20, assignee="Sarah", rtype_raw="Tax: 1040",
              status="Completed"),
        _item("a2", "Acme", "State Return", 20, assignee="Bob", rtype_raw="Tax: 1040"),
    ]
    data = _data(items)
    sarah_page = review.build_staff_page(data, "NLC", gen, "Sarah")
    assert sarah_page["open"] == 0
    assert sarah_page["overdue"] == 0
    assert sarah_page["open_by_type"] == []
    bob_page = review.build_staff_page(data, "NLC", gen, "Bob")
    assert bob_page["open"] == 1
    assert bob_page["overdue"] == 1
    assert bob_page["open_by_type"] == [{"type": "Tax: 1040", "count": 1}]
