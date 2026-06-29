"""Generate a synthetic Karbon-like export CSV for development and testing.

The column names intentionally mimic a real Karbon work export so the mapping
heuristics get exercised. Run:  python sample_data/generate_sample.py
"""
from __future__ import annotations

import csv
import os
from datetime import date, timedelta

# Deterministic pseudo-randomness (no `random` import) so output is stable.
STAFF = ["Dana Whitfield", "James Patel", "Maria Lopez", "Tom Becker", "Aisha Khan"]
CLIENTS = [
    "Acme Holdings", "Briar Cafe LLC", "Cedar Dental", "Delta Logistics",
    "Evergreen Farms", "Foster & Sons", "Greenline Media", "Harbor Realty",
    "Ironwood Mfg", "Juniper Health", "Kestrel Design", "Lumen Tax Co",
]
WORK_TYPES = [
    "1040 Individual Return", "1120 Corp Return", "Financial Statement",
    "Monthly Bookkeeping", "Payroll Filing", "Sales Tax Return", "Audit Engagement",
]
STATUSES = ["Ready to Send", "Awaiting Review", "In Progress", "Sent", "Completed"]


def build_rows(today: date) -> list[dict]:
    rows = []
    n = 70
    for i in range(n):
        staff = STAFF[(i * 7) % len(STAFF)]
        client = CLIENTS[(i * 5) % len(CLIENTS)]
        work = WORK_TYPES[(i * 3) % len(WORK_TYPES)]
        status = STATUSES[(i * 2) % len(STATUSES)]
        # Spread start dates from a few days to ~75 days ago.
        age = 2 + (i * 11) % 74
        start = today - timedelta(days=age)
        rows.append(
            {
                "Work ID": f"W-{1000 + i}",
                "Client Name": client,
                "Work Title": f"{work} - {client.split()[0]}",
                "Work Type": work,
                "Assignee": staff,
                "Status": status,
                "Start Date": start.isoformat(),
            }
        )
    return rows


def write_csv(path: str, today: date | None = None) -> str:
    today = today or date.today()
    rows = build_rows(today)
    fields = ["Work ID", "Client Name", "Work Title", "Work Type", "Assignee", "Status", "Start Date"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return path


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "karbon_sample.csv")
    write_csv(out)
    print(f"Wrote {out}")
