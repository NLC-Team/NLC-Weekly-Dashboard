"""NLC Financial Dashboard - Flask web server.

Run directly (`python src/webapp.py`) or via Run Dashboard.bat.
Opens the browser automatically on startup.
Uses Waitress (production WSGI server) when available, falls back to Flask dev server.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import secrets
import sqlite3
import sys
import tempfile
import threading
import time
import webbrowser
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import matplotlib
matplotlib.use("Agg")
# Chart font: Times New Roman for a classic, professional look.
matplotlib.rcParams["font.family"] = ["Times New Roman", "Times", "serif"]
from flask import (Flask, abort, g, redirect, render_template, request,
                   session, url_for)
from matplotlib.figure import Figure
from werkzeug.security import check_password_hash, generate_password_hash

import config
import mailer
from data import importer, service
from data.store import Store

app = Flask(__name__)
# Random key persisted per-machine (see config.get_or_create_secret_key). This
# replaces the old hardcoded key, which made session cookies forgeable.
app.secret_key = config.get_or_create_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,      # JS can't read the session cookie
    SESSION_COOKIE_SAMESITE="Lax",     # mitigates cross-site request abuse
    # "Remember me" makes the session permanent for this long; without it the
    # cookie is a browser-session cookie that clears when the browser closes.
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
)

# ---- Global singleton store (single-user local app) ----------------------
_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store(config.active_db_path())
    return _store


def _overdue_days() -> int:
    return int(get_store().get_setting("overdue_days", config.DEFAULT_OVERDUE_DAYS))


def _pending_statuses() -> list:
    return list(get_store().get_setting("pending_statuses", []))


# ---- Identity (driven by the logged-in session user) ---------------------

def _current_account() -> dict | None:
    """The logged-in account row, or None. Cached per request on flask.g.
    Session stores the account's display name (its stable primary key)."""
    if not hasattr(g, "_account"):
        name = session.get("auth_user", "")
        acct = get_store().get_account_by_name(name) if name else None
        # Only a fully-active account counts as logged in.
        g._account = acct if (acct and acct["status"] == "active") else None
    return g._account


def _current_user() -> str:
    """Logged-in display name ("" if none)."""
    return session.get("auth_user", "") or ""


def _current_role() -> str:
    acct = _current_account()
    return acct["role"] if acct else ""


def _is_admin() -> bool:
    return _current_role() == "Admin"


def _needs_setup() -> bool:
    """True on first run: no admin who can actually log in exists yet."""
    return get_store().count_active_admins() == 0


# ---- CSRF -----------------------------------------------------------------

def _csrf_token() -> str:
    """Per-session token; generated lazily and reused for the session's life."""
    tok = session.get("csrf_token")
    if not tok:
        tok = secrets.token_hex(16)
        session["csrf_token"] = tok
    return tok


# ---- Login throttle (in-memory, per username) ----------------------------

_FAILED_LOGINS: dict[str, list[float]] = {}
_MAX_FAILS = 5
_FAIL_WINDOW = 900  # seconds (15 min)


def _too_many_fails(username: str) -> bool:
    now = time.time()
    recent = [t for t in _FAILED_LOGINS.get(username, []) if now - t < _FAIL_WINDOW]
    _FAILED_LOGINS[username] = recent
    return len(recent) >= _MAX_FAILS


def _record_fail(username: str) -> None:
    _FAILED_LOGINS.setdefault(username, []).append(time.time())


def _clear_fails(username: str) -> None:
    _FAILED_LOGINS.pop(username, None)


# ---- Password helpers -----------------------------------------------------

def _valid_password(pw) -> bool:
    return isinstance(pw, str) and len(pw) >= 8


def _hash_password(pw: str) -> str:
    # pbkdf2:sha256 is available on every CPython build; scrypt (the Werkzeug
    # default) can be absent, and account hashes are shared across machines.
    return generate_password_hash(pw, method="pbkdf2:sha256")


def _mail_config() -> dict:
    return mailer.merged_config(get_store().get_setting("mail_config", {}))


def _pending_count() -> int:
    """How many sign-up requests are awaiting an admin's approval."""
    return len(get_store().list_pending())


def _admin_emails() -> list[str]:
    """Sign-in emails of every active admin (for access-request notifications)."""
    return [m["email"] for m in get_store().get_staff()
            if m["role"] == "Admin" and m["email"]]


def _notify_admins_of_signup(name: str, email: str) -> None:
    """Email all admins that someone requested access. Best-effort: a mail
    failure must never block the sign-up (the request still lands in the queue)."""
    admins = _admin_emails()
    if not admins:
        logging.warning("New access request from %s <%s> but no admin email is on file.",
                        name, email)
        return
    ok, err = mailer.send_signup_notification(_mail_config(), ", ".join(admins), name, email)
    if not ok:
        logging.warning("Could not notify admins of access request from %s <%s>: %s",
                        name, email, err)


def _safe_next(target: str) -> str:
    """Only allow same-site relative redirects (block open-redirects)."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("overview")


@app.context_processor
def _inject_user():
    """Make the current user/role, staff list and CSRF token available to
    every template (all now driven by the logged-in session user)."""
    acct = _current_account()
    is_admin = bool(acct and acct["role"] == "Admin")
    return {
        "current_user": acct["name"] if acct else "",
        "current_username": acct["username"] if acct else "",
        "current_role": acct["role"] if acct else "",
        "all_staff": get_store().get_staff(),
        "csrf_token": _csrf_token(),
        # Admins are alerted to new sign-up requests in-app (a badge on the Staff
        # menu + a banner), since outbound email can't be relied on here.
        "pending_count": _pending_count() if is_admin else 0,
    }


# ---- Access decorators ----------------------------------------------------

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not _current_account():
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        acct = _current_account()
        if not acct:
            return redirect(url_for("login", next=request.path))
        if acct["role"] != "Admin":
            abort(403)
        return fn(*args, **kwargs)
    return wrapper


def manager_required(fn):
    """Manager or Admin — allowed to change document/project state."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        acct = _current_account()
        if not acct:
            return redirect(url_for("login", next=request.path))
        if acct["role"] not in ("Admin", "Manager"):
            abort(403)
        return fn(*args, **kwargs)
    return wrapper


# ---- Global request gate: CSRF + authentication ---------------------------

_PUBLIC_ENDPOINTS = {"login", "signup", "logout", "setup", "static"}


@app.before_request
def _auth_gate():
    ep = request.endpoint

    # 1) CSRF: every state-changing POST must carry the session token. Checked
    #    first so even unauthenticated POSTs (login/signup/setup) are covered.
    if request.method == "POST":
        token = request.form.get("csrf_token", "")
        if not token or token != session.get("csrf_token"):
            abort(400, description="Invalid or missing CSRF token. Reload the page and try again.")

    if ep == "static" or ep is None:
        return

    # 2) First run: with no admin yet, funnel everyone to the setup screen.
    if _needs_setup():
        if ep != "setup":
            return redirect(url_for("setup"))
        return

    # 3) Public auth pages need no login. Once an admin exists, setup is closed.
    if ep in _PUBLIC_ENDPOINTS:
        if ep == "setup":
            return redirect(url_for("login"))
        return

    # 4) Everything else requires a valid, still-existing, active account.
    if not _current_account():
        session.pop("auth_user", None)
        return redirect(url_for("login", next=request.path))


@app.errorhandler(403)
def _forbidden(_e):
    return render_template("error.html",
                           code=403, title="Not allowed",
                           message="Your account doesn't have permission to view that page."), 403


@app.errorhandler(400)
def _bad_request(e):
    desc = getattr(e, "description", "Bad request.")
    return render_template("error.html", code=400, title="Bad request", message=desc), 400


# ---- Dashboard data + chart cache ----------------------------------------
# service.dashboard_data() pulls every active item and recomputes all aggregates
# on each call; the workload chart re-renders a PNG. Both only change when the
# store's data_version moves (an import or edit), the day rolls over, or the
# overdue threshold changes. Cache on that composite key so repeated page loads
# are served from memory. The lock also collapses a burst of concurrent requests
# into a single recompute rather than letting all 8 Waitress threads redo it.
_cache_lock = threading.Lock()
_data_cache: dict = {"key": None, "data": None}
_chart_cache: dict = {"key": None, "img": None}


def _cache_key() -> tuple:
    return (get_store().data_version, date.today().isoformat(), _overdue_days())


def _get_data() -> dict:
    key = _cache_key()
    with _cache_lock:
        if _data_cache["key"] != key:
            _data_cache["data"] = service.dashboard_data(
                get_store(), date.today(), _overdue_days())
            _data_cache["key"] = key
        return _data_cache["data"]


def _known_statuses() -> list[str]:
    rows = get_store().conn.execute(
        "SELECT DISTINCT last_status FROM items WHERE last_status != '' ORDER BY last_status"
    ).fetchall()
    return [r[0] for r in rows]


def _group_statuses(statuses: list[str]) -> list[tuple[str, list[str]]]:
    """Group statuses by the category before ' - ' (e.g. 'Waiting', 'In Progress')
    so the settings screen shows a few collapsible groups instead of one long
    flat wall of checkboxes."""
    groups: dict[str, list[str]] = {}
    for s in statuses:
        grp = s.split(" - ", 1)[0].strip() if " - " in s else s
        groups.setdefault(grp, []).append(s)
    return [(g, sorted(m)) for g, m in sorted(groups.items(), key=lambda kv: kv[0].lower())]


# ---- Chart helpers -------------------------------------------------------

def _fig_to_b64(fig: Figure) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=96, facecolor="white")
    buf.seek(0)
    data = base64.b64encode(buf.read()).decode()
    fig.clf()
    return data


def _style_ax(ax, title: str):
    """Apply consistent professional styling to a chart axis."""
    ax.set_title(title, fontsize=13, fontweight="bold", color="#0f1923", pad=12)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color("#d1d9e0")
    ax.tick_params(axis="both", colors="#374151", labelsize=10, length=0)
    ax.yaxis.grid(True, color="#e8eef4", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.set_facecolor("white")


def _workload_chart(per_assignee: list) -> str | None:
    try:
        top = per_assignee[:8]
        if not top:
            return None

        # Most overdue staff on the left — that's who needs attention first.
        ordered = sorted(top, key=lambda r: (r["overdue_count"], r["pending_count"]), reverse=True)

        labels = [r["assignee"] for r in ordered]  # full names, no truncation
        pending = [r["pending_count"] for r in ordered]
        overdue = [r["overdue_count"] for r in ordered]
        n = len(labels)
        x = list(range(n))
        w = 0.38  # each column; O left, P right within each group

        # Width scales with the number of staff so columns stay readable.
        fig_w = max(6.0, n * 0.95)
        fig = Figure(figsize=(fig_w, 4.2), facecolor="white")
        ax = fig.add_subplot(111)

        # Charcoal for O (overdue, left), amber for P (pending, right).
        bars_o = ax.bar([xi - w / 2 for xi in x], overdue, width=w,
                        label="Overdue", color="#374151", zorder=3, linewidth=0)
        bars_p = ax.bar([xi + w / 2 for xi in x], pending, width=w,
                        label="Pending", color="#d97706", zorder=3, linewidth=0)

        # Value labels on top of each column; skip zeros to keep it clean.
        ymax = max(pending + overdue + [1])
        for bar in list(bars_o) + list(bars_p):
            h = bar.get_height()
            if h > 0:
                ax.text(bar.get_x() + bar.get_width() / 2, h + ymax * 0.02, str(int(h)),
                        ha="center", va="bottom", fontsize=9, fontweight="700", color="#0f1923")

        # Full names rotated so they never overlap regardless of staff count.
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=10, fontweight="600")
        ax.set_ylim(0, ymax * 1.18)
        _style_ax(ax, "Staff Workload Summary")
        ax.legend(fontsize=10, frameon=False, loc="upper right",
                  ncol=2, handlelength=1.0, handletextpad=0.4, columnspacing=1.2)

        fig.tight_layout(pad=1.6)
        return _fig_to_b64(fig)
    except Exception:
        logging.exception("workload chart failed")
        return None


def _age_chart(age_distribution: list) -> str | None:
    if not age_distribution:
        return None
    labels = [d[0] for d in age_distribution]
    values = [d[1] for d in age_distribution]
    fig = Figure(figsize=(5.8, 3.6), facecolor="white")
    ax = fig.add_subplot(111)
    bars = ax.bar(range(len(labels)), values, color="#1d4ed8",
                  zorder=3, linewidth=0, width=0.6)
    # Gradient-style: tint bars by age severity
    colors = ["#93c5fd", "#60a5fa", "#3b82f6", "#1d4ed8", "#1e40af", "#1e3a8a"]
    for bar, col in zip(bars, colors[:len(bars)]):
        bar.set_color(col)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=0, ha="center", fontsize=10, fontweight="600")
    _style_ax(ax, "Document Age Distribution")
    for b, v in zip(bars, values):
        if v:
            ax.text(b.get_x() + b.get_width() / 2, v + 0.15, str(v),
                    ha="center", va="bottom", fontsize=10, fontweight="700", color="#0f1923")
    fig.tight_layout(pad=1.4)
    return _fig_to_b64(fig)


# ---- Routes --------------------------------------------------------------

def _workload_chart_cached(per_assignee: list) -> str | None:
    """Workload PNG for the current data, rendered at most once per data change."""
    key = _cache_key()
    with _cache_lock:
        if _chart_cache["key"] != key:
            _chart_cache["img"] = _workload_chart(per_assignee)
            _chart_cache["key"] = key
        return _chart_cache["img"]


# ---- Authentication routes -----------------------------------------------

@app.route("/setup", methods=["GET", "POST"])
def setup():
    """First-run bootstrap: create the very first Admin. Reachable only while
    no active admin exists; closed off (redirects to login) afterwards."""
    if not _needs_setup():
        return redirect(url_for("login"))
    error = ""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not name or not email:
            error = "Please enter your name and email."
        elif not config.is_allowed_email(email):
            error = f"Email must be a @{config.ALLOWED_EMAIL_DOMAIN} address."
        elif not _valid_password(password):
            error = "Password must be at least 8 characters."
        elif (owner := get_store().get_account_by_email(email)) and owner["name"] != name:
            error = "That email is already used by another account."
        else:
            try:
                get_store().create_account(name, "Admin", _hash_password(password), "active",
                                           email=email, email_verified=1, today=date.today())
            except sqlite3.IntegrityError:
                # First-run with a leftover account of the same name (e.g. an
                # email-less admin from an earlier version). No usable admin
                # exists yet, so claim that row as this new admin.
                get_store().set_login(name, email, _hash_password(password))
                get_store().upsert_staff(name, "Admin", date.today())  # ensure Admin role
            session.clear()
            session["auth_user"] = name
            session.permanent = True
            return redirect(url_for("overview"))
    return render_template("setup.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    """Sign in with your @company email + the password you chose at sign-up.
    An account must be approved by an admin before it can sign in."""
    if _current_account():
        return redirect(url_for("overview"))
    email = ""
    nxt = request.values.get("next", "")
    error = ""

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "on"
        if _too_many_fails(email):
            error = "Too many failed attempts. Please wait a few minutes and try again."
        else:
            acct = get_store().get_account_by_email(email)
            pw_ok = bool(acct and acct["password_hash"]
                         and check_password_hash(acct["password_hash"], password))
            if not pw_ok:
                _record_fail(email)
                error = "Invalid email or password."  # generic: no user enumeration
            elif acct["status"] != "active":
                error = "Your account is awaiting administrator approval."
            else:
                _clear_fails(email)
                session.clear()  # fresh session on login (prevents fixation)
                session["auth_user"] = acct["name"]
                session.permanent = remember  # Remember me -> 7-day cookie
                return redirect(_safe_next(nxt))

    return render_template("login.html", email=email, next=nxt, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    """Self sign-up: name + company email + a password you choose creates a
    PENDING account. Admins are alerted (in-app + best-effort email); once an
    admin approves it, you can sign in with your email + password."""
    if _current_account():
        return redirect(url_for("overview"))
    error = ""
    done = False
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if not name or not email:
            error = "Please enter your name and email."
        elif not config.is_allowed_email(email):
            error = f"Access is limited to @{config.ALLOWED_EMAIL_DOMAIN} email addresses."
        elif not _valid_password(password):
            error = "Password must be at least 8 characters."
        elif password != confirm:
            error = "The two passwords don't match."
        elif get_store().get_account_by_email(email):
            error = "An account with that email already exists — try signing in."
        else:
            try:
                get_store().create_account(name, "Viewer", _hash_password(password), "pending",
                                           email=email, email_verified=0, today=date.today())
            except sqlite3.IntegrityError:
                return render_template("signup.html",
                                       error="That name or email is already in use.")
            # Best-effort admin heads-up; the reliable alert is the in-app
            # pending badge/banner, so a mail failure never matters here.
            _notify_admins_of_signup(name, email)
            done = True
    return render_template("signup.html", error=error, done=done)


@app.route("/account", methods=["GET", "POST"])
@login_required
def account_password():
    """Manage your own account: the email you sign in with and your password."""
    acct = _current_account()
    error = ""
    msg = ""
    if request.method == "POST":
        # Optional: change the email used to sign in.
        new_email = request.form.get("email", "").strip().lower()
        if new_email and new_email != (acct["email"] or "").lower():
            if not config.is_allowed_email(new_email):
                error = f"Email must be a @{config.ALLOWED_EMAIL_DOMAIN} address."
            else:
                other = get_store().get_account_by_email(new_email)
                if other and other["name"] != acct["name"]:
                    error = "That email is already used by another account."
                else:
                    get_store().set_email(acct["name"], new_email)
                    msg = "Sign-in email updated."
                    acct = _current_account()

        # Password change (only if the fields were filled in).
        current = request.form.get("current", "")
        new = request.form.get("new", "")
        confirm = request.form.get("confirm", "")
        if not error and (current or new or confirm):
            if not acct["password_hash"] or not check_password_hash(acct["password_hash"], current):
                error = "Your current password is incorrect."
            elif not _valid_password(new):
                error = "New password must be at least 8 characters."
            elif new != confirm:
                error = "The new passwords don't match."
            else:
                get_store().set_password(acct["name"], _hash_password(new))
                msg = (msg + " Password updated.").strip()
    return render_template("account_password.html", error=error, msg=msg, acct=_current_account())


# ---- Dashboard routes -----------------------------------------------------

@app.route("/")
@login_required
def overview():
    data = _get_data()
    age_dist = data["age_distribution"]
    max_age_count = max((d[1] for d in age_dist), default=1) or 1
    return render_template("overview.html",
                           data=data,
                           overdue_days=_overdue_days(),
                           workload_img=_workload_chart_cached(data["per_assignee"]),
                           age_dist=age_dist,
                           max_age_count=max_age_count)


@app.route("/person")
@login_required
def person():
    data = _get_data()
    names = sorted({it["assignee"] for it in data["items"]})
    name = request.args.get("name", names[0] if names else "")
    items = sorted(
        [it for it in data["items"] if it["assignee"] == name],
        key=lambda x: x["age_days"], reverse=True,
    )
    ages = [it["age_days"] for it in items]
    stats = {
        "pending": len(items),
        "overdue": sum(1 for it in items if it["overdue"]),
        "max_age": max(ages) if ages else 0,
    }
    return render_template("person.html", names=names, selected=name,
                           items=items, stats=stats)


@app.route("/projects")
@login_required
def projects():
    data = _get_data()
    ft = request.args.get("filter", "All")
    pkey = request.args.get("pkey", "")
    show_done = request.args.get("done", "0") == "1"

    all_proj = data["projects"]
    if show_done:
        # Completed tab: show ONLY completed returns; type filter is disabled.
        relevant = [p for p in all_proj if p["completed"]]
        ft = "All"
    else:
        relevant = [p for p in all_proj if not p["completed"]]

    # Filter chips are built from the specific types actually present in the open
    # returns, so a brand-new type from an import shows up on its own — no fixed list.
    type_chips = sorted({p["return_type"] for p in all_proj if not p["completed"]},
                        key=str.lower)
    counts = {"All": len(relevant)}
    for t in type_chips:
        counts[t] = sum(1 for p in relevant if p["return_type"] == t)

    display = [p for p in relevant if ft == "All" or p["return_type"] == ft]
    if not pkey and display:
        pkey = display[0]["project_key"]

    selected = next((p for p in all_proj if p["project_key"] == pkey), None)
    totals = data["project_totals"]
    # Every type seen anywhere (open or completed) + Unclassified, for the
    # reclassify dropdown; admins can also type a brand-new one in the box.
    all_types = sorted({p["return_type"] for p in all_proj} | {config.UNCLASSIFIED},
                       key=str.lower)
    return render_template("projects.html",
                           display=display, selected=selected,
                           filter_type=ft, show_done=show_done,
                           counts=counts, totals=totals,
                           type_chips=type_chips, all_types=all_types)


@app.route("/projects/doc", methods=["POST"])
@manager_required
def project_doc():
    get_store().set_received(
        request.form["item_key"],
        request.form.get("received") == "1",
        date.today(),
    )
    return redirect(request.referrer or url_for("projects"))


@app.route("/projects/type", methods=["POST"])
@manager_required
def project_type():
    # A typed-in custom value wins over the dropdown, so admins can introduce a
    # brand-new type on the spot (it then joins the list for everyone).
    rtype = request.form.get("custom_type", "").strip() or request.form.get("return_type", "")
    get_store().set_project_type(request.form["pkey"], rtype)
    return redirect(url_for("projects",
                             pkey=request.form["pkey"],
                             filter=request.form.get("filter", "All"),
                             done=request.form.get("done", "0")))


@app.route("/projects/complete", methods=["POST"])
@manager_required
def project_complete():
    pkey = request.form["pkey"]
    data = _get_data()
    p = next((x for x in data["projects"] if x["project_key"] == pkey), None)
    new_state = not (p["completed"] if p else False)
    get_store().set_project_completed(pkey, new_state, date.today())
    # Mark complete -> jump to completed tab; reopen -> jump to open tab.
    # Either way the project lands on the tab where it now lives.
    done = "1" if new_state else "0"
    return redirect(url_for("projects",
                             filter="All",
                             done=done,
                             pkey=pkey))


@app.route("/projects/delete", methods=["POST"])
@admin_required
def project_delete():
    pkey = request.form["pkey"]
    data = _get_data()
    p = next((x for x in data["projects"] if x["project_key"] == pkey), None)
    if p:
        item_keys = [d["item_key"] for d in p["documents"]]
        get_store().delete_project(pkey, item_keys)
    return redirect(url_for("projects",
                             filter=request.form.get("filter", "All"),
                             done=request.form.get("done", "0")))


@app.route("/overdue")
@login_required
def overdue():
    data = _get_data()
    by_person: dict[str, list] = {}
    for it in data["overdue"]:
        by_person.setdefault(it["assignee"], []).append(it)
    # Alphabetical by staff member; each person's items worst-first.
    ranked = sorted(
        ((person, sorted(items, key=lambda x: x["age_days"], reverse=True))
         for person, items in by_person.items()),
        key=lambda kv: kv[0].lower(),
    )
    worst = {person: items[0] for person, items in ranked}
    return render_template("overdue.html",
                           overdue_items=data["overdue"],
                           overdue_days=_overdue_days(),
                           ranked=ranked, worst=worst)


@app.route("/clients")
@admin_required
def clients():
    """Admin-only bulk client deletion page."""
    allowed = _is_admin()
    projects = []
    if allowed:
        data = _get_data()
        projects = sorted(data["projects"], key=lambda p: p["client"].lower())
    return render_template("clients.html", allowed=allowed, projects=projects,
                           msg=request.args.get("msg", ""))


@app.route("/clients/delete", methods=["POST"])
@admin_required
def clients_delete():
    keys = request.form.getlist("pkey")
    data = _get_data()
    by_key = {p["project_key"]: p for p in data["projects"]}
    count = 0
    for k in keys:
        p = by_key.get(k)
        if p:
            get_store().delete_project(k, [d["item_key"] for d in p["documents"]])
            count += 1
    word = "client" if count == 1 else "clients"
    return redirect(url_for("clients", msg=f"Deleted {count} {word}."))


@app.route("/staff")
@admin_required
def staff():
    return render_template("staff.html",
                           members=get_store().get_staff(),
                           pending=get_store().list_pending(),
                           msg=request.args.get("msg", ""),
                           msg_type=request.args.get("mt", "ok"))


@app.route("/staff/add", methods=["POST"])
@admin_required
def staff_add():
    """Admin adds a member directly. Admin-created accounts are active
    immediately (the admin vouches for them); the admin sets an initial password
    to give the person, who can then change it."""
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    role = request.form.get("role", "Viewer")
    if not name or not email:
        return redirect(url_for("staff", msg="Name and email are both required.", mt="err"))
    if not config.is_allowed_email(email):
        return redirect(url_for("staff",
                                msg=f"Email must be a @{config.ALLOWED_EMAIL_DOMAIN} address.", mt="err"))
    if not _valid_password(password):
        return redirect(url_for("staff", msg="Set an initial password of at least 8 characters.", mt="err"))
    try:
        get_store().create_account(name, role, _hash_password(password), "active",
                                   email=email, email_verified=1, today=date.today())
    except sqlite3.IntegrityError:
        return redirect(url_for("staff", msg="That name or email is already in use.", mt="err"))
    return redirect(url_for("staff",
                            msg=f"{name} added as {role}. Give them the password you just set.",
                            mt="ok"))


@app.route("/staff/remove", methods=["POST"])
@admin_required
def staff_remove():
    name = request.form.get("name", "")
    target = next((m for m in get_store().get_staff() if m["name"] == name), None)
    if (target and target["role"] == "Admin" and target["has_login"]
            and get_store().count_active_admins() <= 1):
        return redirect(url_for("staff", msg="You can't remove the last admin.", mt="err"))
    if name:
        get_store().remove_staff(name)
        return redirect(url_for("staff", msg=f"{name} removed.", mt="ok"))
    return redirect(url_for("staff"))


@app.route("/staff/role", methods=["POST"])
@admin_required
def staff_role():
    name = request.form.get("name", "")
    role = request.form.get("role", "Viewer")
    target = next((m for m in get_store().get_staff() if m["name"] == name), None)
    if (target and target["role"] == "Admin" and role != "Admin" and target["has_login"]
            and get_store().count_active_admins() <= 1):
        return redirect(url_for("staff", msg="You can't demote the last admin.", mt="err"))
    if name:
        get_store().upsert_staff(name, role, date.today())
        return redirect(url_for("staff", msg=f"{name} updated to {role}.", mt="ok"))
    return redirect(url_for("staff"))


@app.route("/staff/reset", methods=["POST"])
@admin_required
def staff_reset():
    """Set or reset a member's login (email + password), keyed by name. Use this
    to give someone a new password to sign in with."""
    name = request.form.get("name", "")
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    if not name or not email:
        return redirect(url_for("staff", msg="Name and email are required.", mt="err"))
    if not config.is_allowed_email(email):
        return redirect(url_for("staff",
                                msg=f"Email must be a @{config.ALLOWED_EMAIL_DOMAIN} address.", mt="err"))
    if not _valid_password(password):
        return redirect(url_for("staff", msg="Password must be at least 8 characters.", mt="err"))
    other = get_store().get_account_by_email(email)
    if other and other["name"] != name:
        return redirect(url_for("staff", msg="That email is used by another account.", mt="err"))
    try:
        get_store().set_login(name, email, _hash_password(password))
    except sqlite3.IntegrityError:
        return redirect(url_for("staff", msg="That email is already in use.", mt="err"))
    return redirect(url_for("staff", msg=f"Login updated for {name}. Give them the new password.", mt="ok"))


@app.route("/staff/approve", methods=["POST"])
@admin_required
def staff_approve():
    name = request.form.get("name", "")
    role = request.form.get("role", "Viewer")
    if role not in ("Viewer", "Manager", "Admin"):
        role = "Viewer"
    if name:
        get_store().approve_account(name, role)
        return redirect(url_for("staff", msg=f"Account approved as {role}.", mt="ok"))
    return redirect(url_for("staff"))


@app.route("/staff/reject", methods=["POST"])
@admin_required
def staff_reject():
    name = request.form.get("name", "")
    if name:
        get_store().remove_staff(name)
        return redirect(url_for("staff", msg="Sign-up request rejected.", mt="ok"))
    return redirect(url_for("staff"))


@app.route("/settings")
@admin_required
def settings():
    mc = _mail_config()
    return render_template("settings.html",
                           overdue_days=_overdue_days(),
                           pending_statuses=_pending_statuses(),
                           status_groups=_group_statuses(_known_statuses()),
                           db_path=str(get_store().db_path),
                           mail={"host": mc["host"], "port": mc["port"], "sender": mc["sender"],
                                 "username": mc["username"], "use_tls": mc["use_tls"],
                                 "has_password": bool(mc["password"]),
                                 "configured": mailer.is_configured(mc)},
                           my_email=(_current_account() or {}).get("email") or "",
                           msg=request.args.get("msg", ""),
                           msg_type=request.args.get("mt", "ok"))


@app.route("/settings/mail", methods=["POST"])
@admin_required
def settings_mail():
    """Save SMTP settings. The password is write-only: a blank field keeps the
    stored one, so it's never echoed back to the page."""
    saved = dict(get_store().get_setting("mail_config", {}) or {})
    saved["host"] = request.form.get("host", "").strip() or "smtp.office365.com"
    try:
        saved["port"] = int(request.form.get("port", "587") or 587)
    except ValueError:
        saved["port"] = 587
    saved["sender"] = request.form.get("sender", "").strip()
    saved["username"] = request.form.get("username", "").strip()
    saved["use_tls"] = request.form.get("use_tls") == "on"
    pw = request.form.get("password", "")
    if pw:
        saved["password"] = pw
    get_store().set_setting("mail_config", saved)
    return redirect(url_for("settings", msg="Mail settings saved."))


@app.route("/settings/mail/test", methods=["POST"])
@admin_required
def settings_mail_test():
    to = request.form.get("to", "").strip() or (_current_account() or {}).get("email") or ""
    if not to:
        return redirect(url_for("settings", msg="Enter a recipient address for the test.", mt="err"))
    ok, err = mailer.send_test(_mail_config(), to)
    if ok:
        return redirect(url_for("settings", msg=f"Test email sent to {to}.", mt="ok"))
    return redirect(url_for("settings", msg=f"Test failed: {err}", mt="err"))


@app.route("/settings/days", methods=["POST"])
@admin_required
def settings_days():
    try:
        days = max(1, int(request.form["days"]))
    except (ValueError, KeyError):
        days = _overdue_days()
    get_store().set_setting("overdue_days", days)
    return redirect(url_for("settings", msg=f"Overdue threshold set to {days} days."))


@app.route("/settings/statuses", methods=["POST"])
@admin_required
def settings_statuses():
    statuses = request.form.getlist("statuses")
    get_store().set_setting("pending_statuses", statuses)
    return redirect(url_for("settings", msg="Pending statuses saved."))


# ---- Import flow ---------------------------------------------------------

@app.route("/import", methods=["GET"])
@admin_required
def import_view():
    step = session.get("import_step", 0)
    ctx = session.get("import_ctx", {})
    result = session.pop("import_result", None)
    return render_template("import.html", step=step, ctx=ctx,
                           result=result,
                           logical_fields=config.LOGICAL_FIELDS)


@app.route("/import/upload", methods=["POST"])
@admin_required
def import_upload():
    f = request.files.get("csv_file")
    if not f or not f.filename:
        return redirect(url_for("import_view"))

    fname = f.filename.lower()
    if fname.endswith(".xlsx") or fname.endswith(".xlsm"):
        suffix = ".xlsx"
    elif fname.endswith(".xls"):
        suffix = ".xls"
    else:
        suffix = ".csv"

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    f.save(tmp.name)
    tmp.close()

    try:
        df = importer.load_file(tmp.name)
    except Exception as e:
        try:
            Path(tmp.name).unlink(missing_ok=True)
        except OSError:
            pass
        return render_template("import.html", step=0, ctx={}, result=None,
                               logical_fields=config.LOGICAL_FIELDS,
                               upload_error=f"Could not read file: {e}")

    columns = list(df.columns)
    # Check saved mapping for this CSV layout first
    sig = importer.header_signature(columns)
    saved = get_store().get_mapping(sig)
    guesses = saved if saved else importer.guess_mapping(columns)
    records = importer.apply_mapping(df, guesses)
    all_statuses = importer.distinct_statuses(records)

    session["import_step"] = 1
    session["import_ctx"] = {
        "tmp_path": tmp.name,
        "filename": f.filename,
        "columns": columns,
        "guesses": guesses,
        "all_statuses": all_statuses,
        "row_count": len(df),
        "sig": sig,
    }
    return redirect(url_for("import_view"))


@app.route("/import/run", methods=["POST"])
@admin_required
def import_run():
    ctx = session.get("import_ctx", {})
    tmp_path = ctx.get("tmp_path", "")
    if not tmp_path or not Path(tmp_path).exists():
        session.pop("import_step", None)
        session.pop("import_ctx", None)
        return redirect(url_for("import_view"))

    mapping = {}
    for field, _label, _req in config.LOGICAL_FIELDS:
        col = request.form.get(f"map_{field}", "").strip()
        if col:
            mapping[field] = col

    statuses = request.form.getlist("pending_status")
    if statuses:
        get_store().set_setting("pending_statuses", statuses)

    stats = service.import_csv(
        get_store(), tmp_path, mapping,
        get_store().get_setting("pending_statuses", []),
        date.today(),
    )
    get_store().save_mapping(ctx.get("sig", ""), mapping, date.today())

    try:
        Path(tmp_path).unlink(missing_ok=True)
    except OSError:
        pass

    session.pop("import_step", None)
    session.pop("import_ctx", None)
    session["import_result"] = stats
    return redirect(url_for("import_view"))


@app.route("/import/cancel")
@admin_required
def import_cancel():
    ctx = session.pop("import_ctx", {})
    session.pop("import_step", None)
    try:
        tmp = ctx.get("tmp_path", "")
        if tmp:
            Path(tmp).unlink(missing_ok=True)
    except OSError:
        pass
    return redirect(url_for("import_view"))


# ---- Startup -------------------------------------------------------------

HOST = "127.0.0.1"
PORT = 5000
URL = f"http://{HOST}:{PORT}"


def _server_already_running() -> bool:
    """True if something is already listening on our port (an earlier launch)."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((HOST, PORT)) == 0


def _open_browser():
    import time
    time.sleep(1.8)
    webbrowser.open(URL)


if __name__ == "__main__":
    # If a dashboard is already running, just open a tab to it and exit —
    # this prevents a second server and a second browser tab from opening.
    if _server_already_running():
        logging.info("Dashboard already running — opening existing instance.")
        webbrowser.open(URL)
        sys.exit(0)

    threading.Thread(target=_open_browser, daemon=True).start()
    try:
        from waitress import serve
        logging.info(f"Starting on {URL} (Waitress)")
        serve(app, host=HOST, port=PORT, threads=8,
              channel_timeout=60, cleanup_interval=30)
    except ImportError:
        logging.warning("Waitress not found — falling back to Flask dev server")
        app.run(host=HOST, port=PORT, debug=False, use_reloader=False)
