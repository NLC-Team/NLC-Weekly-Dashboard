from datetime import date

import pandas as pd

from data import importer


def _df():
    return pd.DataFrame(
        {
            "Work ID": ["W-1", "W-2", "W-3"],
            "Client Name": ["Acme", "Briar", "Cedar"],
            "Work Title": ["1040 Acme", "Stmt Briar", "Audit Cedar"],
            "Assignee": ["Sarah", "James", "Sarah"],
            "Status": ["Ready to Send", "Sent", "In Progress"],
            "Start Date": ["2026-06-01", "2026-05-15", ""],
        }
    )


def test_guess_mapping_picks_sensible_columns():
    cols = list(_df().columns)
    m = importer.guess_mapping(cols)
    assert m["assignee"] == "Assignee"
    assert m["client"] == "Client Name"
    assert m["status"] == "Status"
    assert m["item_id"] == "Work ID"
    assert m["date"] == "Start Date"
    assert m["title"] == "Work Title"


def test_apply_mapping_normalises_rows():
    m = importer.guess_mapping(list(_df().columns))
    recs = importer.apply_mapping(_df(), m)
    assert len(recs) == 3
    assert recs[0]["assignee"] == "Sarah"
    assert recs[0]["source_date"] == date(2026, 6, 1)
    assert recs[2]["source_date"] is None  # empty date -> None


def test_item_key_uses_id_when_present():
    m = importer.guess_mapping(list(_df().columns))
    recs = importer.apply_mapping(_df(), m)
    assert recs[0]["item_key"] == "id:w-1"


def test_item_key_hashes_when_no_id():
    df = _df().drop(columns=["Work ID"])
    m = importer.guess_mapping(list(df.columns))
    recs = importer.apply_mapping(df, m)
    assert recs[0]["item_key"].startswith("h:")
    # Same client/title/assignee -> same key (stable across imports).
    recs2 = importer.apply_mapping(df, m)
    assert recs[0]["item_key"] == recs2[0]["item_key"]


def test_filter_pending_case_insensitive():
    m = importer.guess_mapping(list(_df().columns))
    recs = importer.apply_mapping(_df(), m)
    pending = importer.filter_pending(recs, ["ready to send", "in progress"])
    titles = {r["title"] for r in pending}
    assert titles == {"1040 Acme", "Audit Cedar"}  # 'Sent' excluded


def test_filter_pending_empty_means_all():
    m = importer.guess_mapping(list(_df().columns))
    recs = importer.apply_mapping(_df(), m)
    assert len(importer.filter_pending(recs, [])) == 3


def test_distinct_statuses():
    m = importer.guess_mapping(list(_df().columns))
    recs = importer.apply_mapping(_df(), m)
    assert importer.distinct_statuses(recs) == ["In Progress", "Ready to Send", "Sent"]


def test_duplicate_rows_get_distinct_keys_no_id():
    # No ID column + two identical client/title/assignee rows (a "copied" client):
    # both are kept, with distinct keys, so nothing is dropped and a batch insert
    # can't collide. First occurrence keeps the bare hash key (back-compat).
    df = pd.DataFrame({
        "Client Name": ["Acme", "Acme", "Acme"],
        "Work Title": ["1040", "1040", "1040"],
        "Assignee": ["Sarah", "Sarah", "Sarah"],
        "Status": ["In Progress", "In Progress", "In Progress"],
    })
    m = importer.guess_mapping(list(df.columns))
    recs = importer.apply_mapping(df, m)
    keys = [r["item_key"] for r in recs]
    assert len(keys) == 3 and len(set(keys)) == 3          # all kept, all distinct
    assert not keys[0].endswith("#2")                       # first keeps bare key
    assert keys[1].endswith("#2") and keys[2].endswith("#3")
