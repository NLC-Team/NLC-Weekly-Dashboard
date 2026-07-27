from datetime import date, datetime, timedelta

from data import analytics, review

TODAY = date(2026, 6, 29)


def _item(key, client, title, days, assignee="Sarah", rtype_raw="1040 Individual",
          owner="Owen Bradfield", status="Requested"):
    start = TODAY - __import__("datetime").timedelta(days=days)
    return {
        "item_key": key, "client": client, "title": title, "status": status,
        "first_seen": start, "source_date": start, "return_type_raw": rtype_raw,
        "assignee": assignee, "project_key": "c:" + client.lower(),
        "client_owner": owner,
    }


def _data(items, doc_states=None, project_states=None, overdue_days=14):
    """Mirror service.dashboard_data: enrich, fold received/completed out of
    overdue, set each item's effective return_type, and expose data['overdue']."""
    doc_states = doc_states or {}
    project_states = project_states or {}
    enriched = analytics.enrich_items(items, TODAY, overdue_days)
    completed = {k for k, v in project_states.items() if v.get("completed")}
    for it in enriched:
        it["received"] = bool(doc_states.get(it["item_key"]))
        it["completed"] = it.get("project_key") in completed
        if it["received"] or it["completed"]:
            it["overdue"] = False
            it["days_overdue"] = 0
    projects = analytics.build_projects(enriched, doc_states, project_states)
    type_by_pkey = {p["project_key"]: p["return_type"] for p in projects}
    for it in enriched:
        pkey = it.get("project_key") or ("c:" + it["client"].strip().lower())
        it["return_type"] = type_by_pkey.get(pkey)
    return {"projects": projects, "overdue": analytics.overdue_items(enriched)}


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


def test_completed_this_week_window_and_scope():
    gen = datetime(2026, 6, 29, 7, 0)
    ws = review._week_start_sunday(gen.date())
    this_week = ws.isoformat()
    last_week = (ws - timedelta(days=3)).isoformat()
    items = [
        _item("a", "Acme", "W-2", 40, owner="Owen Bradfield"),
        _item("b", "Beta", "1099", 40, owner="Owen Bradfield"),
        _item("c", "Ceta", "K-1", 40, owner="Marcus Lorne"),
    ]
    pstates = {
        "c:acme": {"return_type": "Tax: 1040", "completed": True, "completed_at": this_week},
        "c:beta": {"return_type": "Tax: 1120", "completed": True, "completed_at": last_week},
        "c:ceta": {"return_type": "Tax: 1065", "completed": True, "completed_at": this_week},
    }
    r = review.build_review(_data(items, project_states=pstates), "NLC", gen)
    clients = [c["client"] for c in r["completed_this_week"]]
    assert "Acme" in clients          # completed this week, Owen-owned
    assert "Beta" not in clients      # completed before this Sunday
    assert "Ceta" not in clients      # this week but not Owen-owned
    assert r["week_start"] == ws.isoformat()


def test_completed_this_week_includes_work_title():
    # A client can bundle several documents under one client-level return (see
    # analytics.build_projects); the "Completed this week" title joins their
    # titles the same way assignee_label joins staff names.
    gen = datetime(2026, 6, 29, 7, 0)
    ws = review._week_start_sunday(gen.date())
    this_week = ws.isoformat()
    items = [
        _item("a1", "Acme", "1040 Return", 40, owner="Owen Bradfield"),
        _item("a2", "Acme", "State Return", 40, owner="Owen Bradfield"),
        _item("b1", "Beta", "1099", 40, owner="Owen Bradfield"),
    ]
    pstates = {
        "c:acme": {"return_type": "Tax: 1040", "completed": True, "completed_at": this_week},
        "c:beta": {"return_type": "Tax: 1099", "completed": True, "completed_at": this_week},
    }
    r = review.build_review(_data(items, project_states=pstates), "NLC", gen)
    by_client = {c["client"]: c for c in r["completed_this_week"]}
    assert by_client["Acme"]["title"] == "1040 Return, State Return"
    assert by_client["Beta"]["title"] == "1099"


def test_completed_this_week_resets_from_last_import_not_calendar_week():
    # A completion recorded just before a calendar week flips must not be silently
    # dropped just because nobody viewed the review until the following week -- the
    # window resets from the actual last import date, not a fixed calendar Sunday.
    gen = datetime(2026, 6, 29, 7, 0)
    ws = review._week_start_sunday(gen.date())
    last_import = ws - timedelta(days=3)   # e.g. a Thursday import, in the
                                            # PREVIOUS calendar week relative to `gen`
    items = [_item("a", "Gamma", "W-2", 40, owner="Owen Bradfield")]
    pstates = {
        "c:gamma": {"return_type": "Tax: 1040", "completed": True,
                    "completed_at": last_import.isoformat()},
    }
    data = _data(items, project_states=pstates)

    # Old calendar-week rule (no last_import_date given) would exclude it.
    r_calendar = review.build_review(data, "NLC", gen)
    assert "Gamma" not in [c["client"] for c in r_calendar["completed_this_week"]]

    # Anchored to the actual last import date, it's correctly included.
    r_import = review.build_review(data, "NLC", gen, last_import_date=last_import)
    assert "Gamma" in [c["client"] for c in r_import["completed_this_week"]]
    assert r_import["week_start"] == last_import.isoformat()


def test_completed_this_week_excludes_closed_other():
    # A client closed out ONLY via a "Completed - <word>" status (see
    # analytics.status_is_closed_other) is not a real completion -- even though
    # apply_import_completion stamps project_state.completed_at for it too (the
    # Settings "Completed statuses" list includes those variants), it must never
    # show up in "Completed this week".
    gen = datetime(2026, 6, 29, 7, 0)
    ws = review._week_start_sunday(gen.date())
    items = [_item("a", "ClosedOther", "Extension", 40, owner="Owen Bradfield",
                    status="Completed - Cancelled")]
    pstates = {
        "c:closedother": {"return_type": "Tax: 1040", "completed": True,
                          "completed_source": "import", "completed_at": ws.isoformat()},
    }
    data = _data(items, project_states=pstates)
    r = review.build_review(data, "NLC", gen, last_import_date=ws)
    assert "ClosedOther" not in [c["client"] for c in r["completed_this_week"]]


def test_staff_page_recent_overdue_projects_sorted_freshest_first():
    # "10 most recently overdue projects" = smallest days_overdue first (just
    # crossed the threshold), grouped at PROJECT level -- distinct from
    # top_overdue's document-level "worst first" ordering. Excludes non-overdue
    # projects and other staff members' work.
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


def test_staff_page_recent_overdue_joins_multiple_document_titles():
    # A client can bundle several overdue documents under one client-level
    # return (see analytics.build_projects); the title joins them the same way
    # the Weekly Review PDF's Completed-this-week column does.
    items = [
        _item("a1", "Multi", "1040 Return", 40, assignee="Sarah"),
        _item("a2", "Multi", "State Return", 40, assignee="Sarah"),
    ]
    sp = review.build_staff_page(_data(items), "NLC", datetime(2026, 6, 29, 7, 0), "Sarah")
    assert sp["recent_overdue"][0]["title"] == "1040 Return, State Return"


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


def test_only_nuno_owned_clients_included():
    r = _build([
        _item("a", "Acme", "W-2", 40, owner="Owen Bradfield"),
        _item("b", "Beta", "1099", 60, owner="Marcus Lorne"),      # other owner -> out
        _item("c", "Ceta", "K-1", 50, owner=None),               # no owner -> out
    ])
    assert [t["client"] for t in r["top"]] == ["Acme"]
    assert r["total_overdue"] == 1


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
