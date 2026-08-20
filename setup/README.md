# Team-access setup (turning "works on my machine" into an internal website)

Two scripts turn the dashboard from "works on one machine" into an always-on
internal website the whole firm can open. Run them **on the host that should
serve the dashboard**.

| Script | Needs admin? | What it does |
|--------|:---:|--------------|
| `enable-team-access.ps1` | **Yes** | Opens the firewall for port 5000 (internal only) **and** installs the always-on service. Re-runnable. |
| `verify-team-access.ps1` | No | Read-only health check; changes nothing. Run it to confirm everything is live. |

## Before you start: there is no sign-in

The dashboard has no login. **Anything that can reach port 5000 gets the whole
dashboard**, including client data. So:

- Keep the firewall rule to the Domain and Private profiles only (the script
  does this; it deliberately excludes Public).
- Never port-forward 5000, publish it through a tunnel, or expose it to the
  internet.
- Treat "who is on the internal network" as the entire access-control story.

## How to apply (one elevated run)

1. Log on to the host as an administrator (or have IT do it).
2. Open **PowerShell as Administrator**.
3. Run the script, passing your own domain account and the path to this repo:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\enable-team-access.ps1 `
       -ServiceUser 'YOURDOMAIN\theaccount' `
       -SourceRoot  '\\yourfileserver\share\path\to\AI Dashboard'
   ```
   `-ServiceUser` must be the account whose profile holds the database and the
   WinPython install (see landmine #1 below). Use a UNC path for `-SourceRoot`,
   not a mapped drive letter — an elevated session often has different mappings.
4. When prompted, enter that account's Windows password (see caveat below).
5. It prints the staff URL, e.g. `http://<hostname>:5000`. Confirm with
   `verify-team-access.ps1`.

No reboot is needed — it starts the service immediately and also survives future
reboots.

## Weekly review (view / print / save — no email, no schedule)

The dashboard's **Weekly Review** page shows the top-10 most overdue open returns
firm-wide plus every staff member's overdue returns, always from live data.
**Print / Save as PDF** opens a formatted PDF (generated fresh each time) in a new
tab to print or save; **Download PDF** saves it directly. There is no emailing and
no scheduled job — nothing to install here for it.

## Why it's built this way (two landmines a naive setup hits)

1. **It runs as a specific user, on purpose.** The database and the WinPython
   interpreter live inside one account's profile. The app derives its database
   path from `%LOCALAPPDATA%`, which is per-account — so a service running as
   `SYSTEM` would silently open a **brand-new empty database**. Running as the
   owning account keeps the real data.
2. **It copies the code to `C:\KarbonDashboard` first.** The source normally
   lives on a mapped network drive. A boot-time service with nobody logged in
   usually has no drive mappings, so the script deploys a local copy and runs
   from there — no dependency on the file server being up.

## Caveat: the stored password

Because the service runs "whether logged on or not" as a named account, Windows
stores that account's password with the task. If that password changes (or the
account is disabled/removed), the task stops starting — just re-run
`enable-team-access.ps1` and enter the new password.

*Want to remove that fragility?* It's a small follow-up: add an `NLC_DATA_DIR`
override to `config.py`, copy the database to a machine-wide folder (e.g.
`C:\ProgramData\...`), and run the service as `SYSTEM` (no password, survives
staff changes). It is deliberately **not** done here, because it would move a
live database without being asked.

## Updating the app later

Re-run `enable-team-access.ps1` — it re-copies `src\` from the source and restarts
the service. (An admin action, since it also re-touches the task.)
