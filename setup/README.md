# Team-access setup (the two things that were blocking full team use)

Two scripts turn the dashboard from "works on one machine" into "always-on internal
website the whole firm can open." They must be run **on the host (`THE-HOST`)**.

| Script | Needs admin? | What it does |
|--------|:---:|--------------|
| `enable-team-access.ps1` | **Yes** | Opens the firewall for port 5000 (internal only) **and** installs the always-on service. Re-runnable. |
| `verify-team-access.ps1` | No | Read-only health check; changes nothing. Run it to confirm everything is live. |

## How to apply (one elevated run)

1. Log on to **`THE-HOST`** as an administrator (or have IT do it).
2. Open **PowerShell as Administrator**.
3. Run:
   ```powershell
   powershell -ExecutionPolicy Bypass -File "\\fileserver\Clients\KARBON\Update Project - Sarah's Excels\2025 Tax Season\Summer Interns\AI Dashboard\setup\enable-team-access.ps1"
   ```
4. When prompted, enter the Windows password for **`YOURDOMAIN\serviceaccount`** (see caveat below).
5. It prints the staff URL, e.g. `http://THE-HOST:5000`. Confirm with `verify-team-access.ps1`.

No reboot is needed — it starts the service immediately and also survives future reboots.

## Why it's built this way (two landmines a naive setup hits)

1. **Runs as `serviceaccount`, on purpose.** The database (your 1096 items + the admin
   account), the `secret_key`, and the WinPython interpreter all live in the
   `serviceaccount` profile. The app derives its DB path from `%LOCALAPPDATA%`, which is
   per-account — so a service running as `SYSTEM` would silently open a **brand-new
   empty database**. Running as `serviceaccount` keeps your real data.
2. **Copies the code to `C:\KarbonDashboard` first.** The source lives on `S:\`
   (`\\fileserver\Clients`), a per-login mapped drive. A boot-time service with nobody
   logged in usually has no `S:` drive, so it deploys a local copy and runs from
   there — no dependency on the file server being up.

## Caveat: the stored password

Because the service runs "whether logged on or not" **as `serviceaccount`**, Windows stores
that account's password with the task. If **`serviceaccount`'s password changes** (or the
account is disabled/removed), the task stops starting — just re-run
`enable-team-access.ps1` and enter the new password.

*Want to remove that fragility?* It's a small follow-up: add a `NLC_DATA_DIR` override
to `config.py`, copy the DB to a shared local folder (e.g. `C:\ProgramData\...`), and
run the service as `SYSTEM` (no password, survives staff changes). Ask and it can be
wired up — it's deliberately **not** done here to avoid moving your live database
without your say-so.

## Updating the app later

Re-run `enable-team-access.ps1` — it re-copies `src\` from the share and restarts the
service. (An admin action, since it also re-touches the task.)
