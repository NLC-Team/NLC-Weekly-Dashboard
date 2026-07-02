# IT Help — Hosting the NLC Financial Dashboard for the whole team

*Plain-language brief to hand to IT. The goal: let staff on their own computers open an
internal web dashboard that currently only works on one machine.*

---

## What this is
A small internal web app (Python / Flask) that shows our Karbon pending-documents and
tax-return dashboards. It runs on one Windows machine; staff view it in a normal web browser.
It holds firm and client data, so **it must stay inside our network — not on any public cloud.**

## The problem we're trying to solve
- Right now the app only runs on **one machine** and is only reachable **on that same machine**
  (it listens on `127.0.0.1:5000`, i.e. "localhost only").
- Our staff are on **separate computers / virtual desktops**, so today they can't open it.
- That machine is **locked down**, so we need IT's help to make it reachable and keep it running.

We want it to behave like a simple internal website: one always-on machine runs it, everyone
else opens `http://<server-name>:5000` in their browser. No install needed on staff computers.

---

## What we need IT to do (the fix)

1. **Let the app listen on the network** (not just on itself). This is a one-line change in the
   code (`HOST = "127.0.0.1"` → `HOST = "0.0.0.0"` in `src\webapp.py`) — our developer can make
   it. Flagging it so you know the app will then listen on **port 5000**.

2. **Open the firewall** — allow inbound **TCP port 5000** on the host machine from the
   **internal network only** (please do **not** expose it to the public internet).

3. **Keep it running automatically** — set the app to start on boot and stay running after
   reboots/log-off. Today it only runs while someone is logged in and launches it by hand.
   Options: a **Scheduled Task at startup**, or a **Windows service** (e.g. NSSM). It is launched
   as:
   ```
   %LOCALAPPDATA%\WP\WPy64-313130\python\pythonw.exe   <app-folder>\src\webapp.py
   ```
   (Uses a portable "WinPython" install — no admin/MSI install required for Python itself.)

4. **Give staff the address** to bookmark, e.g. `http://<server-name-or-IP>:5000`.

5. **Where to host it:** if the firm already has a preferred spot for small internal web tools,
   that's ideal. Otherwise the current Windows Server it runs on is fine.

---

## Data & backups
- The database is a **single SQLite file** on the host machine:
  `%LOCALAPPDATA%\KarbonPendingDashboard\dashboard.db`
- **Keep it local on the host machine. Do NOT move it to a network share (e.g. the S: drive).**
  SQLite gets corrupted when accessed over a Windows file share by multiple users.
- **Please back it up:** a **nightly copy of that `.db` file to the S: drive** is safe and
  enough. (Copying the file is fine; *running* the app from a share is not.)

## Security notes
- **Internal only** — do not open port 5000 to the public internet.
- The connection is currently plain **HTTP**, so the login password travels unencrypted on the
  internal network. Acceptable short-term for an internal tool, but ideally put it behind
  **HTTPS** via a reverse proxy (IIS / nginx / Caddy) with a firm certificate — a good phase 2.
- Access is controlled in the app itself: staff sign in with their **@nlcfcpa.com email +
  password**, and new sign-ups require an admin to approve them.

---

## Technical quick facts (for IT)
- **Stack:** Python 3.13 (portable WinPython), Flask + Waitress (production WSGI server), SQLite.
- **Interpreter:** `%LOCALAPPDATA%\WP\WPy64-313130\python\python.exe`
- **App entry point:** `src\webapp.py` — launch with `pythonw src\webapp.py` (or `Run Dashboard.bat`).
- **Port:** 5000 (changeable). **Threads:** 8 (Waitress). **No outbound internet calls** are
  required for the app to function.
- **Note on the `.exe`:** the file `dist\KarbonDashboard.exe` is an **older desktop version**,
  not this web app — please ignore it for this hosting task.

## Optional: outbound email (not required to run)
The app can email admins when someone requests access. This currently **doesn't send**, because
Microsoft 365 is rejecting it — either **Authenticated SMTP is disabled** on the mailbox, or the
server's IP isn't an allowed **Direct Send** sender (it was seen on a Spamhaus block list). This
is **optional**: the app shows the same "new access request" alert inside the dashboard whether
or not email works. If we want the email alerts too, IT would need to either enable Authenticated
SMTP on a sending mailbox, or allow Direct Send from the host's IP.
