from datetime import datetime

import review_pdf


def _busy_rv(n_staff=14, n_types=11):
    """A review big enough that the "Overdue by staff member" cards cannot all
    fit on one page — the condition under which cards used to be split across a
    page break, corrupting their frame and mislaying their name/count."""
    types = [{"type": f"Tax: {1000 + i}", "count": 200 - i} for i in range(n_types)]
    return {
        "firm_name": "NLC Financial",
        "generated_at": datetime(2026, 7, 29, 12, 0),
        "total_overdue": 861,
        "week_start": "2026-07-23",
        "completed_this_week": [
            {"client": f"Client Number {i}", "title": "2025 Year-End Review",
             "return_type": "Year-end review", "assignee": "Owen Bradfield",
             "completed_at": "2026-07-28"}
            for i in range(16)
        ],
        "top": [],
        "recent": [],
        "staff_rows": [
            {"assignee": f"Staff Member {i}", "overdue": 200 - i, "open": 10,
             "completed_week": 1, "overdue_by_type": types}
            for i in range(n_staff)
        ],
        "staff_pages": [],
    }


def test_staff_cards_never_straddle_a_page_break(monkeypatch):
    # A card's name/count are centered, and its frame drawn, from coordinates
    # captured before and after its chips are laid out. Those coordinates are only
    # comparable within ONE page, so a card that starts on page N and finishes on
    # page N+1 produces a negative-height frame and a name centered on nothing.
    pages = {"n": 0}
    orig_new_page = review_pdf._Doc._new_page

    def counting_new_page(self):
        pages["n"] += 1
        orig_new_page(self)

    monkeypatch.setattr(review_pdf._Doc, "_new_page", counting_new_page)

    spans = []
    orig_chip_row = review_pdf._type_chip_row

    def tracing_chip_row(doc, types, indent, x_right=review_pdf.RIGHT):
        before = pages["n"]
        orig_chip_row(doc, types, indent, x_right)
        spans.append((before, pages["n"]))

    monkeypatch.setattr(review_pdf, "_type_chip_row", tracing_chip_row)

    review_pdf.render_pdf(_busy_rv())
    straddled = [s for s in spans if s[0] != s[1]]
    assert straddled == [], f"{len(straddled)} staff card(s) split across a page break"


def test_no_card_frame_is_drawn_with_negative_height(monkeypatch):
    # The direct corruption signature of a split card: Rectangle(..., height<0),
    # which matplotlib draws inverted as a full-page frame whose stray edge cuts
    # through unrelated staff rows below it.
    seen = []
    orig_rect = review_pdf.Rectangle

    def recording_rect(xy, width, height, **kw):
        seen.append(height)
        return orig_rect(xy, width, height, **kw)

    monkeypatch.setattr(review_pdf, "Rectangle", recording_rect)
    review_pdf.render_pdf(_busy_rv())
    assert [h for h in seen if h < 0] == [], "a frame/band was drawn with negative height"


def test_overdue_by_staff_section_starts_on_a_fresh_page(monkeypatch):
    # The section gets its own page rather than being crammed under whatever is
    # left below "Completed this week".
    seen = {}
    orig_text = review_pdf._Doc.text

    def tracing_text(self, x, s, **kw):
        if s == "Overdue by staff member" and "y" not in seen:
            seen["y"] = self.y
        return orig_text(self, x, s, **kw)

    monkeypatch.setattr(review_pdf._Doc, "text", tracing_text)
    review_pdf.render_pdf(_busy_rv())
    assert seen.get("y") == review_pdf.START_Y


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
    # Smoke test: the staff page's right-hand table, "10 most recently overdue
    # projects", is drawn by _stmt_table with neither show_emp nor emp_key (see
    # review_pdf.py's _staff_page), so it renders the plain #/CLIENT/DOCUMENT-
    # WORK/DAYS-OVERDUE columns -- no separate RETURN TYPE or EMPLOYEE column.
    # Each row here is a single document (one per document, not a joined
    # multi-title engagement row) -- make sure a staff page with recent_overdue
    # rows renders without error.
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
            "recent_overdue": [{"rank": 1, "client": "Beta", "title": "1040 Return",
                                "return_type": "Tax: 1040", "days_overdue": 2}],
        }],
    }
    pdf_bytes = review_pdf.render_pdf(rv)
    assert pdf_bytes[:4] == b"%PDF"
    assert len(pdf_bytes) > 500


def test_pdf_marks_a_handoff_row_in_its_own_column():
    """The done table gained an OUTCOME column so a handoff cannot be misread
    as a finished return, and a handoff row still renders."""
    assert "outcome" in review_pdf._DONE_COLS

    rv = {
        "firm_name": "NLC Financial",
        "generated_at": datetime(2026, 6, 29, 7, 0),
        "week_start": "2026-06-23",
        "top_n": 10,
        "total_overdue": 0,
        "completed_this_week": [
            {"client": "Acme", "title": "1040 Return", "return_type": "Tax: 1040",
             "assignee": "Alice", "completed_at": "2026-06-29",
             "kind": "handoff", "handed_to": "Bob"},
            {"client": "Beta", "title": "1120 Return", "return_type": "Tax: 1120",
             "assignee": "Carol", "completed_at": "2026-06-28",
             "kind": "completed", "handed_to": None},
        ],
        "top": [], "recent": [], "per_staff": [], "staff_rows": [],
        "staff_pages": [],
    }
    pdf = review_pdf.render_pdf(rv)
    assert pdf[:4] == b"%PDF"
