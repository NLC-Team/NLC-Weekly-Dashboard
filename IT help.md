# IT Help — Hosting the NLC Financial Dashboard for the whole team

*Plain-language brief to hand to IT. The goal: let staff on their own computers open an
internal web dashboard that currently only works on one machine.*

---

## What this is
A small internal web app (Python / Flask) that shows our Karbon pending-documents and
tax-return dashboards. It runs on one Windows machine; staff view it in a normal web browser.
It holds firm and client data, so **it must stay inside our network — not on any public cloud.**

## The problem we're trying to solve
- The app is **ready to serve the whole network** (it now listens on `0.0.0.0:5000`, all
  interfaces), but two things still block staff from reaching it: the **firewall** isn't open on
  port 5000, and it isn't yet set to **run as an always-on service**.
- Our staff are on **separate computers / virtual desktops**, so they need to reach it over the LAN.
- That machine is **locked down**, so we need IT's help to open the port and keep it running.

We want it to behave like a simple internal website: one always-on machine runs it, everyone
else opens `http://<server-name>:5000` in their browser. No install needed on staff computers.

---

## What we need IT to do (the fix)

1. **Let the app listen on the network** (not just on itself). ✅ **Already done in code** —
   the app now binds to `0.0.0.0` (all interfaces) and listens on **port 5000**. No code change
   needed from you; flagging it so you know the port to open.

2. **Open the firewall** — allow inbound **TCP port 5000** on the host machine from the
   **internal network only** (please do **not** expose it to the public internet).

3. **Keep it running automatically, with NO ONE logged in.** ⭐ *This is the most important
   requirement.* The whole point is that the dashboard must **not depend on any person's session** —
   it should behave like an internal website that's always up, even after reboots and even when
   nobody is signed in to the host machine.

   **Please set it up as a Windows Service** (e.g. **NSSM** — `nssm install KarbonDashboard`), OR,
   if you prefer a Scheduled Task, it **must** be created with **"Run whether user is logged on or
   not"** and **"Run with highest privileges"**, triggered **At startup**.

   > ⚠️ A plain "At log on" Scheduled Task is **not** sufficient — it only runs after someone signs
   > in, which reintroduces the human dependency we're trying to remove. It must run with nobody
   > logged in.

   Either way, launch it as:
   ```
   %LOCALAPPDATA%\WP\WPy64-313130\python\pythonw.exe   <app-folder>\src\webapp.py
   ```
   (Uses a portable "WinPython" install — no admin/MSI install required for Python itself. Note:
   `%LOCALAPPDATA%` resolves to the *service account's* profile, so run the service as the account
   that owns the WinPython folder, or use the full expanded path to `pythonw.exe`.)

4. **Give it the friendly address `NLC Dashboard` / `http://nlcdashboard`.** We'd like staff to
   reach it by an easy name, not an IP-and-port. Two pieces:
   - **DNS:** create an internal DNS record (A record or CNAME) **`nlcdashboard`** → the host
     machine's IP, so `http://nlcdashboard:5000` resolves firm-wide.
   - **Drop the `:5000`:** put a tiny reverse proxy on the host that listens on **port 80** and
     forwards to `127.0.0.1:5000` (IIS with ARR, or nginx/Caddy — Caddy is a one-line config).
     Then the address is simply **`http://nlcdashboard`**. (Optional but nice-to-have; without it
     the address is `http://nlcdashboard:5000`, which also works.)

5. **Please enable HTTPS (`https://nlcdashboard`).** This is now **more than a nice-to-have**: staff
   want to *install/pin the dashboard as its own app* (it's built as an installable web app / PWA),
   and browsers only allow that over **HTTPS** (or on the host itself). If you're already adding the
   reverse proxy in step 4, terminate TLS there with a firm/internal certificate — Caddy can even
   auto-manage an internal CA cert. Over plain HTTP the app still works fully in the browser; only
   the one-click "install as app" is unavailable until HTTPS is in place.

6. **Where to host it:** if the firm already has a preferred spot for small internal web tools,
   that's ideal. Otherwise the current Windows Server it runs on is fine.

---

## Data & backups
- The database is a **single SQLite file** on the host machine:
  `%LOCALAPPDATA%\KarbonPendingDashboard\dashboard.db`
- **Keep it local on the host machine. Do NOT move it to a network share (e.g. the S: drive).**
  SQLite gets corrupted when accessed over a Windows file share by multiple users.
- **Please back it up:** a **nightly copy of that `.db` file to the S: drive** is safe and
  enough. (Copying the file is fine; *running* the app from a share is not.)

## Security notes — please read this one

- **The app has NO sign-in.** There are no accounts and no passwords: whoever can reach
  `http://<host>:5000` sees the whole dashboard, including client data, and can import
  and delete data. Being on the internal network *is* the permission.
- Therefore: **internal only.** Open port 5000 to the Domain/Private profiles and
  **never** to the public internet — no port forwarding, no tunnel, no external proxy.
  If the dashboard ever needs to be reachable from outside, tell us first: it needs an
  authentication layer in front of it before that can happen.
- The connection is plain **HTTP**, so page contents travel unencrypted on the internal
  network. **HTTPS is recommended** — see step 5 above (it also unlocks installing the
  dashboard as an app). Put it behind a reverse proxy (IIS / nginx / Caddy) with a
  firm/internal certificate.

---

## Technical quick facts (for IT)
- **Stack:** Python 3.13 (portable WinPython), Flask + Waitress (production WSGI server), SQLite.
- **Interpreter:** `%LOCALAPPDATA%\WP\WPy64-313130\python\python.exe`
- **App entry point:** `src\webapp.py` — launch with `pythonw src\webapp.py` (or `Run Dashboard.bat`).
- **Port:** 5000 (changeable). **Threads:** 8 (Waitress). **No outbound internet calls** are
  required for the app to function.
- **No outbound email.** The app never sends mail and needs no SMTP relay or mailbox.
- **No `.exe` to deploy.** An older standalone desktop build used to be checked in; it is
  gone. Host `src\webapp.py` as described above — that is the real app.
