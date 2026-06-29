"""Generate a synthetic *document-level* Karbon export for the Tax Returns feature.

Each row is one source document (W-2, 1099, ...) expected from a client for their
return, with a Return Type column so Individual/Business classification can be
demonstrated. Run:  python sample_data/generate_doc_sample.py
"""
from __future__ import annotations

import csv
import os
from datetime import date, timedelta

PREPARERS = ["Dana Whitfield", "James Patel", "Maria Lopez", "Tom Becker"]

# (client, return type) -- mix of individual and business returns.
RETURNS = [
    ("John & Mary Smith", "1040 Individual"),
    ("Robert Chen", "1040 Individual"),
    ("Patel Family", "1040 Individual"),
    ("Diane Foster", "1040 Individual"),
    ("Acme Holdings Inc", "1120 Corporation"),
    ("Briar Cafe LLC", "1065 Partnership"),
    ("Cedar Dental PC", "1120 Corporation"),
    ("Delta Logistics LLC", "1065 Partnership"),
    ("Evergreen Farms", "1040 Individual"),
    ("Harbor Realty Group", "1120 Corporation"),
]

INDIVIDUAL_DOCS = [
    "W-2 Wages", "1099-INT Interest", "1099-DIV Dividends", "1099-B Brokerage",
    "1098 Mortgage Interest", "1095-A Health Insurance", "Charitable Receipts",
    "1099-R Retirement",
]
BUSINESS_DOCS = [
    "Prior Year Return", "Year-End Trial Balance", "Bank Statements", "1099-NEC Issued",
    "Payroll Reports", "Fixed Asset / Depreciation Schedule", "Loan Statements",
    "Merchant Processing Statements",
]
STATUSES = ["Requested", "Awaiting Client"]


def build_rows(today: date) -> list[dict]:
    rows = []
    wid = 5000
    for ri, (client, rtype) in enumerate(RETURNS):
        is_business = "1040" not in rtype
        pool = BUSINESS_DOCS if is_business else INDIVIDUAL_DOCS
        n_docs = 4 + ((ri * 3) % 5)  # 4-8 documents per return
        preparer = PREPARERS[ri % len(PREPARERS)]
        for di in range(n_docs):
            doc = pool[di % len(pool)]
            age = 5 + ((ri * 7 + di * 13) % 70)  # 5-74 days since requested
            requested = today - timedelta(days=age)
            rows.append(
                {
                    "Work ID": f"DOC-{wid}",
                    "Client Name": client,
                    "Document": doc,
                    "Return Type": rtype,
                    "Assignee": preparer,
                    "Status": STATUSES[(ri + di) % len(STATUSES)],
                    "Date Requested": requested.isoformat(),
                }
            )
            wid += 1
    return rows


def write_csv(path: str, today: date | None = None) -> str:
    today = today or date.today()
    rows = build_rows(today)
    fields = ["Work ID", "Client Name", "Document", "Return Type", "Assignee", "Status", "Date Requested"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return path


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "karbon_doc_sample.csv")
    write_csv(out)
    print(f"Wrote {out}")
