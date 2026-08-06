"""The import reads a FIXED set of Karbon columns (importer.CANONICAL_COLUMNS).

User directive 2026-08-06: only the nine columns below are ever read; every other
column in an export is ignored no matter what it is called, and `item_id` /
`project` stay deliberately unmapped. If a column is dropped from a future
export the import must carry on with the remaining ones rather than fail.
"""
from data import importer

# The real header of the "All Work" export in use (10 columns).
ALL_WORK = [
    "Client Name", "Client Owner", "Work Title", "Work Type", "Assignee",
    "Status", "Start Date", "Due Date", "Completed Date UTC",
    "Last Status Change Date UTC",
]

# The real header of the richer 26-column "Karbon Work Export".
RICH = [
    "Key", "Client Name", "Client ID", "Client Manager", "Client Owner",
    "Client Group", "Work Title", "Work Type", "Assignee", "Status",
    "Start Date", "Due Date", "Deadline Date", "Completed Date UTC",
    "Last Status Change Date UTC", "Repeat Frequency", "Budget (Minutes)",
    "Budget (USD)", "Actual (Minutes)", "Actual (USD)",
    "Budget Remaining (Minutes)", "Budget Remaining (USD)", "Fee Type",
    "Fee (USD)", "Planned Week", "Progress (%)",
]

EXPECTED = {
    "assignee": "Assignee",
    "client": "Client Name",
    "client_owner": "Client Owner",
    "title": "Work Title",
    "status": "Status",
    "date": "Start Date",
    "due_date": "Due Date",
    "completed_date": "Completed Date UTC",
    "return_type": "Work Type",
}


def test_maps_the_nine_karbon_columns():
    assert importer.canonical_mapping(ALL_WORK) == EXPECTED


def test_ignores_every_other_column_in_the_file():
    # 26 columns in, still exactly the same nine read.
    assert importer.canonical_mapping(RICH) == EXPECTED


def test_never_maps_work_id_or_project():
    # 'Client ID' and 'Key' both look like id columns to the guesser; neither may
    # become item_id, or a client's documents would collapse onto one key.
    m = importer.canonical_mapping(RICH + ["Work ID", "Project", "Engagement"])
    assert "item_id" not in m
    assert "project" not in m


def test_prefers_client_owner_over_client_manager():
    # Both columns exist in the rich export; client_owner drives the Weekly
    # Review's owner scope, so it must be the real "Client Owner".
    assert importer.canonical_mapping(RICH)["client_owner"] == "Client Owner"


def test_carries_on_when_a_column_is_dropped():
    without_due = [c for c in ALL_WORK if c != "Due Date"]
    m = importer.canonical_mapping(without_due)
    assert "due_date" not in m
    assert len(m) == 8
    assert m["status"] == "Status"
    assert m["completed_date"] == "Completed Date UTC"


def test_carries_on_when_several_columns_are_dropped():
    m = importer.canonical_mapping(["Client Name", "Work Title", "Assignee", "Status"])
    assert m == {"client": "Client Name", "title": "Work Title",
                 "assignee": "Assignee", "status": "Status"}


def test_matches_column_names_case_and_whitespace_insensitively():
    m = importer.canonical_mapping(["  client name ", "WORK TITLE", "assignee",
                                    "Status", "completed date utc"])
    # The value must be the column name AS IT APPEARS in the file, so the
    # dataframe lookup in apply_mapping still finds it.
    assert m["client"] == "  client name "
    assert m["title"] == "WORK TITLE"
    assert m["completed_date"] == "completed date utc"


def test_unknown_header_maps_nothing():
    assert importer.canonical_mapping(["foo", "bar", "baz"]) == {}


def test_canonical_columns_covers_exactly_the_nine_fields():
    assert set(importer.CANONICAL_COLUMNS) == set(EXPECTED)
