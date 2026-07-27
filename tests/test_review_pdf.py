from datetime import datetime

import review_pdf


def test_render_pdf_includes_completed_this_week_work_title():
    # Smoke test: the "Completed this week" table now has a WORK TITLE column
    # between CLIENT and RETURN TYPE (see _DONE_COLS) -- make sure a row with a
    # joined multi-document title renders without error.
    rv = {
        "firm_name": "NLC Financial",
        "generated_at": datetime(2026, 6, 29, 7, 0),
        "total_overdue": 0,
        "week_start": "2026-06-28",
        "completed_this_week": [
            {"client": "Acme", "title": "1040 Return, State Return",
             "return_type": "Tax: 1040", "assignee": "Sarah",
             "completed_at": "2026-06-29"},
        ],
        "top": [],
        "recent": [],
        "staff_rows": [],
        "staff_pages": [],
    }
    pdf_bytes = review_pdf.render_pdf(rv)
    assert pdf_bytes[:4] == b"%PDF"
    assert len(pdf_bytes) > 500


def test_render_pdf_staff_page_recent_overdue_projects():
    # Smoke test: the staff page's right-hand table now shows "10 most recently
    # overdue projects" with BOTH a DOCUMENT / WORK column and a RETURN TYPE
    # column (instead of the old "most recent statements" DOCUMENT / WORK /
    # OPENED shape) -- make sure a staff page with recent_overdue rows renders
    # without error.
    rv = {
        "firm_name": "NLC Financial",
        "generated_at": datetime(2026, 6, 29, 7, 0),
        "total_overdue": 1,
        "week_start": "2026-06-28",
        "completed_this_week": [],
        "top": [],
        "recent": [],
        "staff_rows": [{"assignee": "Sarah", "overdue": 1, "open": 2,
                        "completed_week": 0, "overdue_by_type": []}],
        "staff_pages": [{
            "firm_name": "NLC Financial",
            "generated_at": datetime(2026, 6, 29, 7, 0),
            "week_start": "2026-06-28",
            "staff": "Sarah",
            "found": True,
            "completed_week": 0,
            "open": 2,
            "open_by_type": [{"type": "Tax: 1040", "count": 2}],
            "overdue": 1,
            "overdue_by_type": [{"type": "Tax: 1040", "count": 1}],
            "top_overdue": [{"rank": 1, "client": "Acme", "title": "W-2",
                             "days_overdue": 10}],
            "recent_overdue": [{"rank": 1, "client": "Beta", "title": "1040 Return, State Return",
                                "return_type": "Tax: 1040", "days_overdue": 2}],
        }],
    }
    pdf_bytes = review_pdf.render_pdf(rv)
    assert pdf_bytes[:4] == b"%PDF"
    assert len(pdf_bytes) > 500
