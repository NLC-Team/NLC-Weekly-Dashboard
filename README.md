# Karbon Pending Documents Dashboard

A Windows desktop dashboard for an accounting firm. Drop in a CSV export from
Karbon and instantly see:

- **How many pending documents** each person has to send to clients
- **Who has the most on their plate** (workload leaderboard + charts)
- **How long** each document/project has been open or worked on
- **Which statements are overdue** and dragging — with a per-person alert list

The whole team can use it, and it works from a single `.exe` — no install, no
admin rights, no Python required on the user's machine.

---

## For team members: just run it

1. Get **`KarbonDashboard.exe`** (in the `dist` folder, or wherever IT shares it).
2. Double-click it.
3. Click **Import**, choose your Karbon CSV export, confirm the column mapping,
   tick which statuses count as "pending to send", and click **Import now**.
4. Use the left menu: **Overview**, **By person**, **Overdue**, **Settings**.

That's it. Your column mapping and status choices are remembered, so future
imports are one click.

### Day-to-day usage notes
- **Overdue threshold** is adjustable in **Settings** (default: 14 days).
- **Re-import regularly** (e.g. weekly). The app tracks history: an item keeps
  its original "first seen" date, so *days open* keeps growing across imports
  even if your CSV has no date column. Anything that stops appearing is treated
  as sent/completed.
- **Shared team history:** in **Settings → Data storage location**, point the
  database at a shared network folder so everyone's imports build up in one
  place. Leave it at the default for a personal copy.

---

## What the columns mean (mapping)

Karbon exports can have any column names, so on first import you map your columns
onto these fields (the app pre-guesses them for you):

| Field | Required | Meaning |
|-------|----------|---------|
| Assignee / staff member | yes | Who owns the work |
| Client | yes | The client it's for |
| Document / work title | yes | Name of the statement/work |
| Status | yes | Used to decide what's "pending to send" |
| Start / created / due date | no | If present, used to compute days open |
| Karbon work ID | no | Stable key so the same item is tracked across imports |

If there's no date column, the app falls back to tracking how long each item
keeps reappearing in your imports.

---

## For whoever maintains/rebuilds it (developer notes)

### Project layout
```
src/
  main.py            App entry: window, sidebar nav, shared state
  config.py          Paths, logical fields, defaults, app-config persistence
  data/
    importer.py      CSV load, column mapping, pending-status filtering
    store.py         SQLite: settings, saved mappings, item history
    analytics.py     Pure functions: age, overdue, per-staff aggregates
    service.py       Glue: import pipeline + dashboard data
  ui/
    theme.py         Colours, fonts, ttk styling
    widgets.py       KPI cards, sortable table, matplotlib chart canvas
    import_view.py / overview_view.py / person_view.py /
    overdue_view.py / settings_view.py
sample_data/
  generate_sample.py  Writes a synthetic Karbon-like CSV for testing
tests/                pytest suite (analytics, importer, store)
build.spec            PyInstaller one-file spec
requirements.txt
```

### Environment
This machine has no system Python (MSI installs are blocked by policy). A
portable **WinPython** lives at:
```
%LOCALAPPDATA%\WP\WPy64-313130\python\python.exe
```
All commands below use that interpreter. (To recreate it: download the WinPython
"dot" self-extractor from github.com/winpython/winpython and run it — it just
extracts files, no installer.)

### Common commands
```powershell
$py = "$env:LOCALAPPDATA\WP\WPy64-313130\python\python.exe"

# install deps (already done once)
& $py -m pip install -r requirements.txt

# run the app from source
$env:PYTHONPATH = "src"; & $py src\main.py

# regenerate sample data
& $py sample_data\generate_sample.py

# run tests
& $py -m pytest tests -q

# rebuild the standalone exe -> dist\KarbonDashboard.exe
& $py -m PyInstaller build.spec --noconfirm
```

### Where data is stored
- Default database + config: `%LOCALAPPDATA%\KarbonPendingDashboard\`
- The chosen database path is remembered in `app_config.json` there.

### Design notes
- **Age** prefers the real CSV date; otherwise uses how long we've tracked the
  item (`first_seen`). See `data/analytics.py::item_age_days`.
- **Item identity** uses the Karbon work ID when mapped, else a hash of
  client+title+assignee, so the same item is matched across weekly imports.
- All analytics are pure functions (no DB/UI), which is why they're unit-tested
  directly.
