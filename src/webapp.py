"""NLC Dashboard - Flask web server.

Run directly (`python src/webapp.py`) or via Run Dashboard.bat.
Opens the browser automatically on startup.
Uses Waitress (production WSGI server) when available, falls back to Flask dev server.
"""
from __future__ import annotations

import base64
import gzip
import io
import logging
import os
import re
import secrets
import sqlite3
import sys
import tempfile
import threading
import time
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import matplotlib
matplotlib.use("Agg")
# Chart font: Times New Roman for a classic, professional look. "Times" (no
# "New Roman") isn't a real installed family, so it never resolves -- listing
# it here just makes every chart text lookup fail once before falling through
# to serif, which is slow (findfont isn't cached across calls) and spammy.
matplotlib.rcParams["font.family"] = ["Times New Roman", "serif"]
from flask import (Flask, Response, abort, g, redirect, render_template, request,
                   send_from_directory, session, url_for)
from matplotlib.figure import Figure

import config
import review_pdf
import review_xlsx
from data import analytics, importer, review, service
from data.store import Store

# The app normally runs as a windowless pythonw.exe (no console), so the
# console-only logging set up above is invisible in practice -- an unhandled
# exception (e.g. during import) leaves no trace anywhere. Add a file handler
# now that config's data dir is available, so there's always somewhere to look.
_log_file_handler = logging.FileHandler(config.default_log_path(), encoding="utf-8")
_log_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logging.getLogger().addHandler(_log_file_handler)

app = Flask(__name__)
# Random key persisted per-machine (see config.get_or_create_secret_key). There
# are no logins, but the session cookie still carries the CSRF token, so it must
# be signed with a key that isn't guessable.
app.secret_key = config.get_or_create_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,      # JS can't read the session cookie
    SESSION_COOKIE_SAMESITE="Lax",     # mitigates cross-site request abuse
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    # Largest accepted request body. The only big uploads are import CSVs/Excels
    # (a full Karbon export is a few MB); anything bigger gets a clean 413
    # instead of buffering an arbitrarily large body into memory.
    MAX_CONTENT_LENGTH=30 * 1024 * 1024,
    # Re-check template files on every render so edits show up on a plain
    # browser refresh — no server restart needed (Waitress doesn't use the
    # Flask dev server's reloader, so this wouldn't happen by default).
    TEMPLATES_AUTO_RELOAD=True,
)
app.jinja_env.auto_reload = True

# ---- Global singleton store (single-user local app) ----------------------
_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store(config.active_db_path())
        # One-time sweep for reassignments that predate handoff detection (see
        # Store.backfill_handoffs). Self-guarding, so this is a cheap no-op on
        # every start after the first.
        _store.backfill_handoffs()
    return _store


def _overdue_days() -> int:
    return int(get_store().get_setting("overdue_days", config.DEFAULT_OVERDUE_DAYS))


def _pending_statuses() -> list:
    return list(get_store().get_setting("pending_statuses", []))


def _completed_statuses() -> list:
    """Statuses that mean a return is finished. On import, a return whose active
    documents are ALL in one of these is auto-moved to the Completed tab."""
    return list(get_store().get_setting("completed_statuses", []))


# ---- Identity -------------------------------------------------------------
# There is no sign-in. The dashboard is served on the firm's internal network
# and every visitor gets the whole dashboard, so there is no per-visitor
# identity to record. The audit trail logs the requesting machine's IP, which is
# the only thing we can actually know about who did something.

def _client_ip() -> str:
    """The requesting machine's IP, as recorded in the audit trail.

    Read straight from the socket. Forwarded headers (X-Forwarded-For and
    friends) are deliberately NOT trusted: nothing proxies this app, so anyone
    on the LAN could set them and write whatever they liked into the audit log.
    """
    return request.remote_addr or ""


# ---- CSRF -----------------------------------------------------------------

def _csrf_token() -> str:
    """Per-session token; generated lazily and reused for the session's life."""
    tok = session.get("csrf_token")
    if not tok:
        tok = secrets.token_hex(16)
        session["csrf_token"] = tok
    return tok


def _csp_nonce() -> str:
    """A fresh per-request nonce for the Content-Security-Policy. Generated once
    per request and cached on flask.g so the value in the CSP header matches the
    `nonce=` on every inline <script>. Must be per-request (never per-session), or
    it stops being a meaningful guard against injected inline script."""
    if not hasattr(g, "_csp_nonce"):
        g._csp_nonce = secrets.token_urlsafe(16)
    return g._csp_nonce


# ---- Audit trail ----------------------------------------------------------

def _audit(action: str, detail: str = "", actor: str | None = None) -> None:
    """Append to the audit trail (from where, what). With no sign-in there is no
    user name to record, so the IP column carries the "who"."""
    get_store().log_event(
        datetime.now().isoformat(timespec="seconds"),
        actor or "",
        _client_ip(), action, detail)


# ---- Template filters -----------------------------------------------------
# Audit timestamps are stored ISO (e.g. "2026-07-09T14:24:07"). Show them to
# people as a date over a 12-hour (AM/PM) clock. Built by hand, not strftime:
# the non-padded codes ("%-I"/"%-d") aren't portable and fail on Windows.
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _parse_ts(ts):
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


@app.template_filter("audit_date")
def _audit_date(ts):
    dt = _parse_ts(ts)
    return f"{_MONTHS[dt.month - 1]} {dt.day}, {dt.year}" if dt else (ts or "—")


@app.template_filter("audit_time")
def _audit_time(ts):
    """12-hour clock with AM/PM, e.g. '2:24:07 PM'."""
    dt = _parse_ts(ts)
    if not dt:
        return ""
    hour12 = dt.hour % 12 or 12
    meridiem = "AM" if dt.hour < 12 else "PM"
    return f"{hour12}:{dt.minute:02d}:{dt.second:02d} {meridiem}"


@app.template_filter("audit_span")
def _audit_span(first_ts, last_ts):
    """Compact date range for an archived week, e.g. 'Jul 6 – 12, 2026' or
    'Jun 29 – Jul 5, 2026' when it straddles two months. When every event fell
    on a single day, show just that one date (no dash)."""
    a, b = _parse_ts(first_ts), _parse_ts(last_ts)
    if not a or not b:
        return _audit_date(first_ts)
    if (a.year, a.month, a.day) == (b.year, b.month, b.day):
        return f"{_MONTHS[a.month - 1]} {a.day}, {a.year}"
    if a.month == b.month and a.year == b.year:
        return f"{_MONTHS[a.month - 1]} {a.day} – {b.day}, {b.year}"
    return f"{_MONTHS[a.month - 1]} {a.day} – {_MONTHS[b.month - 1]} {b.day}, {b.year}"


@app.context_processor
def _inject_globals():
    """Make the per-request security tokens available to every template."""
    return {
        "csrf_token": _csrf_token(),
        "csp_nonce": _csp_nonce(),
    }


@app.template_global()
def static_url(filename: str) -> str:
    """A cache-busted URL for a file under /static.

    Appends the file's last-modified time as ?v=<mtime>. Static assets are served
    with a long, immutable Cache-Control (see _no_store_dynamic), so the browser
    reuses them from disk on every tab without a round trip; changing the ?v=
    (i.e. editing the file) is what makes a new version reach clients. Falls back
    to a plain URL if the file can't be stat'd."""
    url = url_for("static", filename=filename)
    try:
        mtime = int((Path(app.static_folder) / filename).stat().st_mtime)
        return f"{url}?v={mtime}"
    except OSError:
        return url


# Content-Security-Policy, built per request so script-src can pin a fresh nonce.
#   - script-src 'self' + per-request nonce, NO 'unsafe-inline': every inline
#     <script> carries nonce="{{ csp_nonce }}", and there are no inline event
#     handlers (onclick/onchange/...) left — they were moved to addEventListener —
#     so injected inline script without the nonce simply won't run. This is the
#     real XSS backstop behind Jinja's autoescaping.
#   - style-src keeps 'unsafe-inline': the templates use inline style="" attributes
#     throughout, and CSP nonces do not apply to style attributes, so removing it
#     would mean a large presentational rewrite for little security gain (style
#     injection is far less dangerous than script injection).
#   - img-src 'self' data:: charts are base64 PNGs and the favicon is a data: SVG.
#   - Everything else same-origin: no plugins, no <base> hijack, forms post only
#     here, and the app can't be framed (clickjacking; X-Frame-Options backs this
#     up for older browsers).
def _build_csp(nonce: str) -> str:
    return ("default-src 'self'; "
            "img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; "
            f"script-src 'self' 'nonce-{nonce}'; "
            "connect-src 'self'; "
            "font-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'")


@app.after_request
def _no_store_dynamic(resp):
    """Never let the browser cache dynamic pages/data. This is a live, multi-user
    dashboard: after someone reclassifies a return (or imports, marks received,
    etc.) every tab must reflect it on the next load, not show a browser-cached
    copy. Static brand assets (/static, manifest) stay cacheable — the service
    worker handles those. Mirrors sw.js's 'always live from network' policy."""
    path = request.path or ""
    # Cacheable, versioned assets: /static (icons + app.css, cache-busted by
    # static_url's ?v=<mtime>) and /charts (chart PNGs, cache-busted by
    # ?v=<data_version>). Everything else is live, per-user data — never cached,
    # so a reclassify/import/mark-received shows on the next load, not a stale copy.
    cacheable = path.startswith("/static/") or path.startswith("/charts/")
    if not (cacheable or path == "/manifest.webmanifest"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    elif path.startswith("/static/"):
        # Immutable brand assets + app.css. The ?v=<mtime> makes any edit a new
        # URL, so a year-long cache is safe and skips even a revalidation round
        # trip. Set explicitly to override Flask's conservative default of
        # "no-cache" (which would force a revalidation on every navigation).
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    # /charts/* set their own (data-version-keyed) Cache-Control in the route.

    # Baseline security headers on every response (pages AND static assets):
    #   - CSP: see _CSP above — the main XSS/clickjacking/injection defence.
    #   - nosniff: stop the browser guessing a non-declared content type.
    #   - frame DENY: no embedding the app in an <iframe> (clickjacking).
    #   - Referrer-Policy: don't leak the current URL to any other origin.
    #   - Permissions-Policy: this app needs none of these device APIs; deny all.
    resp.headers.setdefault("Content-Security-Policy", _build_csp(_csp_nonce()))
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Permissions-Policy",
                            "geolocation=(), microphone=(), camera=(), "
                            "payment=(), usb=(), interest-cohort=()")
    # No HSTS: the dashboard is served over plain HTTP on the LAN, and pinning
    # browsers to HTTPS for an origin with no certificate would lock them out.

    _gzip_response(resp)
    return resp


# Text responses (HTML/CSS/JS/JSON/SVG) are gzipped when the client accepts it.
# The dashboard's pages are large — a full returns/person list is several hundred
# KB of markup — so this cuts what crosses the wire by ~80%, which is what other
# computers on the LAN (and over the Cloudflare tunnel) feel most. PNGs/other
# already-compressed types and streamed static files are left untouched.
_GZIP_TYPES = ("text/html", "text/css", "text/plain", "application/javascript",
               "text/javascript", "application/json", "image/svg+xml")


def _gzip_response(resp) -> None:
    try:
        if "gzip" not in request.headers.get("Accept-Encoding", "").lower():
            return
        if resp.direct_passthrough or resp.status_code >= 300:
            return
        if resp.headers.get("Content-Encoding"):
            return
        ctype = (resp.content_type or "").split(";", 1)[0].strip().lower()
        if ctype not in _GZIP_TYPES:
            return
        body = resp.get_data()
        if len(body) < 1024:            # not worth it for tiny responses
            return
        resp.set_data(gzip.compress(body, 6))
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Content-Length"] = str(len(resp.get_data()))
        resp.headers.add("Vary", "Accept-Encoding")
    except Exception:
        # Compression is a nice-to-have; never let it break a response.
        logging.exception("gzip failed; sending uncompressed")


# ---- Global request gate: CSRF -------------------------------------------
# There is no login gate. Every page is open to anyone who can reach the server,
# which is why the server must stay on the internal network (see BIND_HOST at the
# bottom of this file) — the database holds client data.

@app.before_request
def _csrf_gate():
    """Every state-changing POST must carry this session's CSRF token.

    Still needed without logins: it stops another site the user has open from
    silently POSTing to the dashboard (deleting clients, running an import) just
    because the user's browser can reach it.
    """
    if request.method == "POST":
        token = request.form.get("csrf_token", "")
        if not token or token != session.get("csrf_token"):
            abort(400, description="Invalid or missing CSRF token. Reload the page and try again.")


@app.errorhandler(400)
def _bad_request(e):
    desc = getattr(e, "description", "Bad request.")
    return render_template("error.html", code=400, title="Bad request", message=desc), 400


@app.errorhandler(413)
def _too_large(_e):
    return render_template("error.html", code=413, title="File too large",
                           message="That upload is bigger than the 30 MB limit. "
                                   "Export a smaller file and try again."), 413


# ---- Installable web app (PWA) -------------------------------------------
# Served from the site root (not /static/) so the manifest's and service
# worker's scope covers the whole app ("/"). A service worker can only control
# pages at or below its own path, so sw.js must live at the root. Both are
# public (browsers fetch them before login) — see _PUBLIC_ENDPOINTS.

@app.route("/manifest.webmanifest")
def manifest():
    resp = send_from_directory(app.static_folder, "manifest.webmanifest")
    resp.headers["Content-Type"] = "application/manifest+json"
    return resp


@app.route("/sw.js")
def service_worker():
    resp = send_from_directory(app.static_folder, "sw.js")
    resp.headers["Content-Type"] = "application/javascript"
    # Allow a file physically served from /sw.js to control the entire "/" scope.
    resp.headers["Service-Worker-Allowed"] = "/"
    # The worker script itself must not be cached, or clients get stuck on an
    # old version and never see updates.
    resp.headers["Cache-Control"] = "no-cache"
    return resp


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
    ax.set_title(title, fontsize=13, fontweight="bold", color="#16202e", pad=12)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color("#d9d2c4")
    ax.tick_params(axis="both", colors="#55606f", labelsize=10, length=0)
    ax.yaxis.grid(True, color="#eee9df", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.set_facecolor("white")


def _workload_chart(overdue_items: list) -> str | None:
    """Overdue statements per employee, STACKED by the specific statement type
    that makes up each column (e.g. Tax: 1040, Accounting/Bookkeeping). One
    column per employee who has overdue work, worst-first; each coloured segment
    is that type's count, a legend maps colour -> type, and the grand total sits
    on top. Lets you see how many of what each person is behind on."""
    try:
        from collections import Counter

        if not overdue_items:
            return None

        # Per employee -> Counter of {statement type: overdue count}.
        by_person: dict[str, Counter] = {}
        overall = Counter()
        for it in overdue_items:
            who = (it.get("assignee") or "").strip() or "Unassigned"
            if who.lower() == "(unassigned)":
                who = "Unassigned"
            t = it["return_type"]
            by_person.setdefault(who, Counter())[t] += 1
            overall[t] += 1
        if not by_person:
            return None

        # Employees worst-first; statement types most-common-first (stable colours).
        people = sorted(by_person, key=lambda p: sum(by_person[p].values()), reverse=True)
        types = [t for t, _ in overall.most_common()]
        # Anchored on the firm's letterhead navy + green so the most common
        # statement types read in brand colour, then distinct hues after.
        palette = ["#1a3f8f", "#4a9d4f", "#d97706", "#b91c1c", "#7c3aed",
                   "#0891b2", "#db2777", "#65a30d", "#c2410c", "#4b5563"]
        colors = {t: palette[i % len(palette)] for i, t in enumerate(types)}

        n = len(people)
        x = list(range(n))
        totals = [sum(by_person[p].values()) for p in people]
        ymax = max(totals + [1])

        fig_w = max(8.2, n * 1.15)
        fig = Figure(figsize=(fig_w, 5.9), facecolor="white")
        ax = fig.add_subplot(111)

        bottoms = [0] * n
        for t in types:
            vals = [by_person[p].get(t, 0) for p in people]
            ax.bar(x, vals, bottom=bottoms, width=0.62, color=colors[t],
                   label=t, zorder=3, linewidth=0)
            # Count inside each segment that's tall enough to hold the text.
            for xi, v, b in zip(x, vals, bottoms):
                if v > 0 and v / ymax >= 0.045:
                    ax.text(xi, b + v / 2, str(v), ha="center", va="center",
                            fontsize=13, fontweight="700", color="white")
            bottoms = [b + v for b, v in zip(bottoms, vals)]

        # Grand total on top of each employee's column.
        for xi, tot in zip(x, totals):
            ax.text(xi, tot + ymax * 0.02, str(tot), ha="center", va="bottom",
                    fontsize=14, fontweight="700", color="#16202e")

        ax.set_xticks(x)
        ax.set_xticklabels(people, rotation=35, ha="right")
        ax.set_ylim(0, ymax * 1.18)
        _style_ax(ax, "Overdue Statements by Employee")
        ax.set_title(ax.get_title(), fontsize=18)
        ax.tick_params(axis="y", labelsize=13)
        # Set explicitly (not via tick_params, which _style_ax already applied to
        # both axes above) so only these labels are affected, and pushed down a
        # bit for breathing room at the larger size.
        ax.tick_params(axis="x", pad=10)
        for lbl in ax.get_xticklabels():
            lbl.set_fontsize(16)
            lbl.set_fontweight("700")
        # Legend outside on the right; _fig_to_b64 saves with bbox_inches='tight'
        # so it's included without squeezing the bars.
        ax.legend(fontsize=14, frameon=False, loc="upper left",
                  bbox_to_anchor=(1.01, 1.0), title="Statement type",
                  title_fontsize=15, handlelength=1.2, handletextpad=0.5)

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
    bars = ax.bar(range(len(labels)), values, color="#1a3f8f",
                  zorder=3, linewidth=0, width=0.6)
    # Tint bars from light to deep navy by age severity (oldest = firm navy).
    colors = ["#c3cee8", "#9fb0da", "#7288c4", "#48609f", "#2c4a86", "#1a3f8f"]
    for bar, col in zip(bars, colors[:len(bars)]):
        bar.set_color(col)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=0, ha="center", fontsize=10, fontweight="700")
    _style_ax(ax, "Document Age Distribution")
    for b, v in zip(bars, values):
        if v:
            ax.text(b.get_x() + b.get_width() / 2, v + 0.15, str(v),
                    ha="center", va="bottom", fontsize=10, fontweight="700", color="#16202e")
    fig.tight_layout(pad=1.4)
    return _fig_to_b64(fig)


# ---- Routes --------------------------------------------------------------

def _workload_chart_cached(overdue_items: list) -> str | None:
    """Overdue chart PNG for the current data, rendered at most once per change."""
    key = _cache_key()
    with _cache_lock:
        if _chart_cache["key"] != key:
            _chart_cache["img"] = _workload_chart(overdue_items)
            _chart_cache["key"] = key
        return _chart_cache["img"]


@app.route("/charts/workload.png")
def workload_chart_png():
    """The 'Overdue statements by employee' chart as a standalone PNG.

    Served as its own image instead of a base64 blob inlined into the Overview
    HTML: the browser caches the picture and reuses it on repeat visits, and the
    Overview page itself gets ~tens of KB lighter (it's re-sent on every load
    because dynamic pages are no-store). The ?v=<data_version> the template
    appends busts the cache the instant the underlying data changes."""
    data = _get_data()
    b64 = _workload_chart_cached(data["overdue"])
    if not b64:
        abort(404)  # nobody overdue → no chart (Overview shows the "caught up" note)
    resp = app.response_class(base64.b64decode(b64), mimetype="image/png")
    # Private (this visitor's browser only, never a shared cache) and
    # version-keyed, so it can be cached hard without ever going stale.
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


# ---- Dashboard routes -----------------------------------------------------

@app.route("/")
def overview():
    data = _get_data()
    age_dist = data["age_distribution"]
    max_age_count = max((d[1] for d in age_dist), default=1) or 1
    return render_template("overview.html",
                           data=data,
                           overdue_days=_overdue_days(),
                           # The chart is fetched as its own cacheable image
                           # (see workload_chart_png); pass only whether one
                           # exists and a version to cache-bust its URL.
                           has_workload=bool(_workload_chart_cached(data["overdue"])),
                           chart_version=get_store().data_version,
                           age_dist=age_dist,
                           max_age_count=max_age_count)


@app.route("/person")
def person():
    data = _get_data()
    names = sorted({it["assignee"] for it in data["items"]})
    # Default view is All staff; a specific assignee narrows to their items.
    name = request.args.get("name", "__all__")
    show_all = name in ("", "__all__") or name not in names
    shown = data["items"] if show_all else [it for it in data["items"] if it["assignee"] == name]
    # Clients A→Z (case-insensitive); oldest-first within the same client.
    items = sorted(shown, key=lambda x: (x["client"].lower(), -x["age_days"]))
    ages = [it["age_days"] for it in items]
    stats = {
        "pending": len(items),
        "overdue": sum(1 for it in items if it["overdue"]),
        "max_age": max(ages) if ages else 0,
    }
    return render_template("person.html", names=names,
                           selected=("" if show_all else name),
                           show_all=show_all, items=items, stats=stats)


@app.route("/projects")
def projects():
    data = _get_data()
    ft = request.args.get("filter", "All")
    pkey = request.args.get("pkey", "")
    # Which view: "0" open (default), "1" completed, "other" completed-other
    # (returns closed via a "Completed - <word>" status). Kept in the `done` param
    # so existing 0/1 links stay valid.
    view = request.args.get("done", "0")
    if view not in ("0", "1", "other"):
        view = "0"
    show_done = view == "1"
    show_closed_other = view == "other"
    sel_year = request.args.get("year", "")
    sel_month = request.args.get("month", "")
    staff = request.args.get("staff", "")

    all_proj = data["projects"]
    all_staff_names = sorted({it["assignee"] for it in data["items"]})

    # --- Staff statements mode --------------------------------------------
    # Reached from the Overview "Statement types" pills (or the Staff dropdown):
    # show ONE employee's individual statements, optionally narrowed to a single
    # type. Counts here match the Overview pills — both count items per assignee
    # per normalised type.
    if staff in all_staff_names:
        s_items = [it for it in data["items"] if it["assignee"] == staff]
        type_counts: dict[str, int] = {}
        for it in s_items:
            t = it["return_type"]
            type_counts[t] = type_counts.get(t, 0) + 1
        staff_chips = sorted(type_counts, key=str.lower)
        staff_counts = {"All": len(s_items)}
        staff_counts.update(type_counts)
        shown = (s_items if ft == "All" else
                 [it for it in s_items if it["return_type"] == ft])
        staff_items = sorted(shown, key=lambda x: (
            x["return_type"], x["client"].lower(), -x["age_days"]))
        return render_template(
            "projects.html", staff_mode=True, staff=staff, staff_items=staff_items,
            all_staff_names=all_staff_names, filter_type=ft,
            type_chips=staff_chips, counts=staff_counts,
            # Placeholders so the shared template never hits an undefined var.
            display=[], selected=None, show_done=False,
            show_closed_other=False, view="0",
            totals=data["project_totals"], all_types=[],
            years=[], sel_year="", sel_month="")

    if show_done:
        # Completed tab: show ONLY genuinely-completed returns (exact "Completed").
        # Type filtering still works here, so the per-type numbers stay useful.
        relevant = [p for p in all_proj if p["completed"]]
    elif show_closed_other:
        # Completed - other: returns closed only via a "Completed - <word>" status.
        relevant = [p for p in all_proj if p.get("closed_other")]
    else:
        relevant = [p for p in all_proj if p["open"]]

    # Filter chips are built from the types actually present in whichever set is
    # shown (open returns, or completed ones when "Show closed" is on), so the
    # per-type counts on the chips always match the list — including in the closed
    # view — and a brand-new type from an import shows up on its own, no fixed list.
    type_chips = sorted({p["return_type"] for p in relevant}, key=str.lower)
    counts = {"All": len(relevant)}
    for t in type_chips:
        counts[t] = sum(1 for p in relevant if p["return_type"] == t)

    # Years present in THIS tab's returns (open, or completed), newest first, for
    # the "opened" filter dropdown — so the options always match what's shown.
    years = sorted({p["opened_year"] for p in relevant if p["opened_year"]}, reverse=True)
    # Drop a year that isn't offered (e.g. from a stale/shared URL) so the
    # dropdown selection and the applied filter never disagree.
    if sel_year and (not sel_year.isdigit() or int(sel_year) not in years):
        sel_year = ""

    display = [p for p in relevant if ft == "All" or p["return_type"] == ft]
    # Month/year "opened" filter — narrows to returns opened in that period.
    if sel_year:
        display = [p for p in display if str(p["opened_year"]) == sel_year]
    if sel_month:
        display = [p for p in display if str(p["opened_month"]) == sel_month]
    # Pick the default-selected return AFTER filtering so it's one that's shown.
    if not pkey and display:
        pkey = display[0]["project_key"]

    selected = next((p for p in all_proj if p["project_key"] == pkey), None)
    totals = data["project_totals"]
    # Every type seen anywhere (open or completed) + Unclassified, for the
    # reclassify dropdown; admins can also type a brand-new one in the box.
    all_types = sorted({p["return_type"] for p in all_proj} | {config.UNCLASSIFIED},
                       key=str.lower)
    # AJAX select/tick (page.js sends X-Requested-With: fetch): return just the
    # detail panel + the one selected row, not the whole list. The page splices
    # these in via swapDetailPanel()/syncRow(), so a click costs a few KB instead
    # of re-sending the entire (up to ~0.5 MB) returns page every time.
    if request.headers.get("X-Requested-With") == "fetch":
        return render_template("projects_ajax.html",
                               selected=selected, filter_type=ft,
                               show_done=show_done, show_closed_other=show_closed_other,
                               view=view, all_types=all_types,
                               sel_year=sel_year, sel_month=sel_month)
    return render_template("projects.html",
                           staff_mode=False, staff="", staff_items=None,
                           all_staff_names=all_staff_names,
                           display=display, selected=selected,
                           filter_type=ft, show_done=show_done,
                           show_closed_other=show_closed_other, view=view,
                           counts=counts, totals=totals,
                           type_chips=type_chips, all_types=all_types,
                           years=years, sel_year=sel_year, sel_month=sel_month)


@app.route("/projects/doc", methods=["POST"])
def project_doc():
    get_store().set_received(
        request.form["item_key"],
        request.form.get("received") == "1",
        date.today(),
    )
    # The page ticks documents via fetch and then refreshes the detail panel
    # itself, so an AJAX call just needs a cheap OK — no full re-render/redirect.
    if request.headers.get("X-Requested-With") == "fetch":
        return "", 204
    return redirect(request.referrer or url_for("projects"))


@app.route("/projects/type", methods=["POST"])
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
def project_delete():
    pkey = request.form["pkey"]
    data = _get_data()
    p = next((x for x in data["projects"] if x["project_key"] == pkey), None)
    if p:
        item_keys = [d["item_key"] for d in p["documents"]]
        get_store().delete_project(pkey, item_keys)
        _audit("project_deleted", f"{p['client']} ({len(item_keys)} items)")
    return redirect(url_for("projects",
                             filter=request.form.get("filter", "All"),
                             done=request.form.get("done", "0")))


@app.route("/overdue")
def overdue():
    data = _get_data()
    view = request.args.get("view", "overdue")
    if view not in ("overdue", "completed"):
        view = "overdue"

    # Completed is counted per CLIENT (return), not per document: a client that
    # finished several documents is one completed return, and only a client whose
    # work is genuinely all done counts -- see analytics.completed_projects_by_assignee.
    completed_total = data["project_totals"]["completed_total"]

    if view == "completed":
        by_person = analytics.completed_projects_by_assignee(data["projects"])
        # Alphabetical by staff member; each person's returns alphabetical by
        # client -- there's no reliable per-document completion date to rank by
        # (age_days keeps counting from when work opened even after it closes),
        # so client name is the most scannable order.
        counts = {person: len(rows) for person, rows in by_person.items()}
        ranked = sorted(
            ((person, analytics.group_by_client_owner(
                sorted(rows, key=lambda p: p["client"].lower())))
             for person, rows in by_person.items()),
            key=lambda kv: kv[0].lower(),
        )
        worst = {}
    else:
        by_person: dict[str, list] = {}
        for it in data["overdue"]:
            by_person.setdefault(it["assignee"], []).append(it)
        # Alphabetical by staff member; each person's items worst-first, where
        # "worst" = most days overdue (past the threshold), not just longest open.
        counts = {person: len(items) for person, items in by_person.items()}
        sorted_by_person = {person: sorted(items, key=lambda x: x["days_overdue"], reverse=True)
                             for person, items in by_person.items()}
        worst = {person: items[0] for person, items in sorted_by_person.items()}
        ranked = sorted(
            ((person, analytics.group_by_client_owner(items))
             for person, items in sorted_by_person.items()),
            key=lambda kv: kv[0].lower(),
        )

    # Optional ?person= deep-link (from clicking an employee's name). Ignore an
    # unknown name / someone with no items in this view so the page never
    # lands empty.
    selected_person = request.args.get("person", "")
    if selected_person not in by_person:
        selected_person = ""
    return render_template("overdue.html",
                           view=view,
                           overdue_items=data["overdue"],
                           completed_total=completed_total,
                           overdue_days=_overdue_days(),
                           counts=counts,
                           ranked=ranked, worst=worst,
                           selected_person=selected_person)


@app.route("/clients")
def clients():
    """Bulk client deletion page."""
    data = _get_data()
    projects = sorted(data["projects"], key=lambda p: p["client"].lower())
    return render_template("clients.html", projects=projects,
                           msg=request.args.get("msg", ""))


@app.route("/clients/delete", methods=["POST"])
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
    if count:
        _audit("clients_deleted", f"{count} {word} bulk-deleted")
    return redirect(url_for("clients", msg=f"Deleted {count} {word}."))


# The staff directory is the firm's roster, not an account list: there are no
# logins, so a member is just a name and a role label. The roster drives the
# staff dropdowns and is separate from the assignee names that arrive in an
# import (those come from the Karbon data itself).
_ROLES = ("Viewer", "Manager", "Admin")


@app.route("/staff")
def staff():
    return render_template("staff.html",
                           members=get_store().get_staff(),
                           roles=_ROLES,
                           msg=request.args.get("msg", ""),
                           msg_type=request.args.get("mt", "ok"))


@app.route("/staff/add", methods=["POST"])
def staff_add():
    name = request.form.get("name", "").strip()
    role = request.form.get("role", "Viewer")
    if role not in _ROLES:
        role = "Viewer"
    if not name:
        return redirect(url_for("staff", msg="Enter the person's name.", mt="err"))
    if any(m["name"].lower() == name.lower() for m in get_store().get_staff()):
        return redirect(url_for("staff", msg=f"{name} is already on the list.", mt="err"))
    get_store().upsert_staff(name, role, date.today())
    _audit("staff_added", f"{name} as {role}")
    return redirect(url_for("staff", msg=f"{name} added as {role}.", mt="ok"))


@app.route("/staff/remove", methods=["POST"])
def staff_remove():
    name = request.form.get("name", "")
    if name:
        get_store().remove_staff(name)
        _audit("staff_removed", name)
        return redirect(url_for("staff", msg=f"{name} removed.", mt="ok"))
    return redirect(url_for("staff"))


@app.route("/staff/role", methods=["POST"])
def staff_role():
    name = request.form.get("name", "")
    role = request.form.get("role", "Viewer")
    if role not in _ROLES:
        role = "Viewer"
    if name:
        get_store().upsert_staff(name, role, date.today())
        _audit("role_changed", f"{name} -> {role}")
        return redirect(url_for("staff", msg=f"{name} updated to {role}.", mt="ok"))
    return redirect(url_for("staff"))


@app.route("/audit")
def audit():
    """Security audit trail: logins, failures, account/credential changes,
    imports, deletions, settings edits. Newest first.

    The live log holds only the CURRENT week; opening this page rolls any
    older events into the weekly archive below (admin-only). Rotation is lazy
    (on view) — there's no separate scheduler to drift or miss."""
    store = get_store()
    moved = store.rotate_audit(date.today())
    if moved:
        _audit("audit_archived", f"{moved} event(s) rolled into the weekly archive")
    return render_template("audit.html",
                           events=store.recent_events(3000),
                           weeks=store.archived_weeks(),
                           this_week=store._week_label(date.today()))


@app.route("/audit/archive/<week>")
def audit_archive(week):
    """View one archived week's events."""
    if not re.fullmatch(r"\d{4}-W\d{2}", week or ""):
        abort(404)
    store = get_store()
    return render_template("audit_archive.html", week=week,
                           events=store.archived_events(week),
                           weeks=store.archived_weeks())


@app.route("/settings")
def settings():
    return render_template("settings.html",
                           overdue_days=_overdue_days(),
                           completed_statuses=_completed_statuses(),
                           all_statuses=_known_statuses(),
                           db_path=str(get_store().db_path),
                           msg=request.args.get("msg", ""),
                           msg_type=request.args.get("mt", "ok"))


@app.route("/settings/days", methods=["POST"])
def settings_days():
    try:
        days = max(1, int(request.form["days"]))
    except (ValueError, KeyError):
        days = _overdue_days()
    get_store().set_setting("overdue_days", days)
    _audit("settings_changed", f"overdue_days={days}")
    return redirect(url_for("settings", msg=f"Overdue threshold set to {days} days."))


@app.route("/settings/completed-statuses", methods=["POST"])
def settings_completed_statuses():
    statuses = request.form.getlist("completed_statuses")
    get_store().set_setting("completed_statuses", statuses)
    _audit("settings_changed", f"completed_statuses ({len(statuses)} selected)")
    return redirect(url_for("settings",
                            msg="Completed statuses saved. Returns whose documents are all "
                                "in one of these will move to Done on the next import."))


# ---- Weekly review -------------------------------------------------------
# A firm-wide "what to finish this week" snapshot: the top-10 most overdue open
# returns, then every staff member's overdue returns. It reflects the live data
# at the moment you open it. There is no emailing/scheduling — an admin views it
# on screen and prints (or saves) the PDF on demand; /review.pdf renders the same
# report build_current_review() shows, so the printout matches the page exactly.

def _last_import_date() -> date | None:
    """The date of the most recent import, or None if nothing's been imported yet.
    "Completed this week" resets from this date, not a fixed calendar Sunday."""
    last = get_store().last_import()
    if not last:
        return None
    try:
        return date.fromisoformat(str(last["imported_at"])[:10])
    except (ValueError, TypeError):
        return None


def build_current_review() -> dict:
    """The weekly review for right now, from the same cached dashboard data the
    rest of the app reads."""
    return review.build_review(_get_data(), config.FIRM_NAME, datetime.now(),
                               last_import_date=_last_import_date())


def build_current_review_pdf() -> dict:
    """The review for the PDF: the same summary as build_current_review PLUS a
    `staff_pages` list — one build_staff_page() per staff row — so the printout
    can carry each staff member's own landscape detail page. Built here (not in
    build_review) so the on-screen main page doesn't pay for detail it renders
    per-staff on demand instead."""
    data = _get_data()
    gen = datetime.now()
    last_import = _last_import_date()
    rv = review.build_review(data, config.FIRM_NAME, gen, last_import_date=last_import)
    rv["staff_pages"] = [review.build_staff_page(data, config.FIRM_NAME, gen, s["assignee"],
                                                 last_import_date=last_import)
                         for s in rv["staff_rows"]]
    return rv


def build_current_review_workbook() -> dict:
    """The per-staff Excel worklists for right now, off the same cached dashboard
    data every other view reads — so the workbook matches the screen, with the
    two narrow exceptions documented on review._staff_doc_stats (a due-today
    overdue count; someone who only completed work this week gets no sheet)."""
    return review.build_staff_workbook(_get_data(), config.FIRM_NAME, datetime.now())


@app.route("/review")
def weekly_review():
    return render_template("review.html", review=build_current_review())


@app.route("/review/staff")
def weekly_review_staff():
    """One staff member's weekly-review detail page, reached by clicking a name on
    the review. Name comes in as a query arg (staff names have spaces/periods, so a
    path segment would need escaping); build_staff_page normalizes it."""
    name = request.args.get("name", "")
    staff = review.build_staff_page(_get_data(), config.FIRM_NAME, datetime.now(), name,
                                    last_import_date=_last_import_date())
    return render_template("review_staff.html", staff=staff)


@app.route("/review.pdf")
def weekly_review_pdf():
    """The current review as a printable PDF, generated on demand from live data.

    Served INLINE by default so clicking opens it in the browser's PDF viewer,
    where the admin can print or save. `?download=1` forces a file download."""
    pdf = review_pdf.render_pdf(build_current_review_pdf())
    fname = f"weekly-review-{date.today().isoformat()}.pdf"
    disposition = "attachment" if request.args.get("download") else "inline"
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": f'{disposition}; filename="{fname}"'})


XLSX_MIMETYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@app.route("/review.xlsx")
def weekly_review_xlsx():
    """The review as an Excel workbook with ONE TAB PER STAFF MEMBER — their open
    work, grouped by work type, most overdue first (see review.build_staff_workbook).

    Always a download: unlike the PDF there's nothing useful to view inline, and
    browsers can't render a spreadsheet anyway."""
    book = review_xlsx.render_xlsx(build_current_review_workbook())
    fname = f"weekly-review-{date.today().isoformat()}.xlsx"
    return Response(book, mimetype=XLSX_MIMETYPE,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ---- Import flow ---------------------------------------------------------

@app.route("/import", methods=["GET"])
def import_view():
    step = session.get("import_step", 0)
    ctx = session.get("import_ctx", {})
    result = session.pop("import_result", None)
    return render_template("import.html", step=step, ctx=ctx,
                           result=result,
                           logical_fields=config.LOGICAL_FIELDS,
                           completed_saved=_completed_statuses())


@app.route("/import/upload", methods=["POST"])
def import_upload():
    f = request.files.get("csv_file")
    if not f or not f.filename:
        # Previously a silent redirect back to this same page -- indistinguishable
        # from the page just not responding. Say what actually happened.
        return render_template("import.html", step=0, ctx={}, result=None,
                               logical_fields=config.LOGICAL_FIELDS,
                               upload_error="No file was selected. Choose a file, then click Upload & continue.")

    fname = f.filename.lower()
    if fname.endswith(".xlsx") or fname.endswith(".xlsm"):
        suffix = ".xlsx"
    elif fname.endswith(".xls"):
        suffix = ".xls"
    else:
        suffix = ".csv"

    # Saved under the app's own stable data directory, NOT the OS temp dir: on
    # this deployment (Remote Desktop Services) tempfile.gettempdir() resolves
    # to a numbered per-session folder that can go stale mid-wizard (e.g. if the
    # app process restarts between "Upload" and "Run import"), silently losing
    # the file the user just uploaded. See config.default_import_upload_dir.
    tmp_path = config.default_import_upload_dir() / f"{secrets.token_hex(8)}{suffix}"
    f.save(tmp_path)

    try:
        df = importer.load_file(str(tmp_path))
    except Exception as e:
        logging.exception("Import upload: could not read %s", f.filename)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return render_template("import.html", step=0, ctx={}, result=None,
                               logical_fields=config.LOGICAL_FIELDS,
                               upload_error=f"Could not read file: {e}")

    columns = list(df.columns)
    # The mapping is FIXED (importer.CANONICAL_COLUMNS), not guessed and not
    # remembered per file layout: only those nine Karbon columns are read and
    # every other column is ignored, however many the file has. A column that is
    # missing simply goes unmapped and the rest still import. The old
    # guess+saved-mapping overlay is gone -- on an unfamiliar export it could
    # mis-map (e.g. "Client ID" as the work ID, or "Client Manager" as the client
    # owner, which silently changes the Weekly Review's owner scope).
    sig = importer.header_signature(columns)
    mapping = importer.canonical_mapping(columns)
    records = importer.apply_mapping(df, mapping)
    all_statuses = importer.distinct_statuses(records)

    session["import_step"] = 1
    session["import_ctx"] = {
        "tmp_path": str(tmp_path),
        "filename": f.filename,
        "columns": columns,
        # `guesses` keeps its name because the mapping step's template reads it;
        # it is now the canonical mapping, shown read-only.
        "guesses": mapping,
        "missing_columns": [importer.CANONICAL_COLUMNS[f]
                            for f in importer.CANONICAL_COLUMNS
                            if f not in mapping],
        "ignored_columns": [c for c in columns if c not in set(mapping.values())],
        "all_statuses": all_statuses,
        "row_count": len(df),
        "sig": sig,
    }
    return redirect(url_for("import_view"))


@app.route("/import/run", methods=["POST"])
def import_run():
    ctx = session.get("import_ctx", {})
    tmp_path = ctx.get("tmp_path", "")
    if not tmp_path or not Path(tmp_path).exists():
        # Previously a silent reset straight back to step 0 -- indistinguishable
        # from the button just not working. Explain what happened: the uploaded
        # file is gone (e.g. the server restarted while this wizard was open).
        session.pop("import_step", None)
        session.pop("import_ctx", None)
        return render_template("import.html", step=0, ctx={}, result=None,
                               logical_fields=config.LOGICAL_FIELDS,
                               upload_error="Your uploaded file is no longer available "
                                            "(the server may have restarted). Please "
                                            "upload the file again.")

    # The mapping is hard-coded, so the posted form no longer decides what is
    # read -- it is rebuilt from the file's own header against
    # importer.CANONICAL_COLUMNS. Derived here rather than trusted from the
    # session so a stale/tampered ctx cannot widen what gets imported.
    mapping = importer.canonical_mapping(ctx.get("columns") or [])

    # No status filtering on import (every row is stored). We only remember which
    # statuses mean "completed" so finished returns land in the Done tab.
    completed = request.form.getlist("completed_status")
    get_store().set_setting("completed_statuses", completed)

    try:
        stats = service.import_csv(
            get_store(), tmp_path, mapping, [], date.today(),
            completed_statuses=completed,
        )
    except Exception:
        # Previously unhandled: a blank/generic error page with no clue what
        # happened, and (running as windowless pythonw.exe) nothing logged
        # anywhere to check afterward. Now: logged to config.default_log_path(),
        # and the user stays on the mapping step (their column choices are kept
        # in ctx) so they can just retry "Run import" without re-uploading --
        # re-running the same file is safe, upsert_items is keyed by item_key.
        logging.exception("Import run failed for %s", ctx.get("filename", "?"))
        return render_template("import.html", step=1, ctx=ctx, result=None,
                               logical_fields=config.LOGICAL_FIELDS,
                               completed_saved=completed,
                               run_error="Something went wrong while importing this file. "
                                         "Your column mapping is unchanged -- try Run "
                                         "import again. If it keeps failing, tell Ben "
                                         "and check the log at "
                                         f"{config.default_log_path()}.")
    get_store().save_mapping(ctx.get("sig", ""), mapping, date.today())
    _audit("import_run", f"{ctx.get('filename', '?')}: {stats.get('new', 0)} new, "
                         f"{stats.get('updated', 0)} updated")

    try:
        Path(tmp_path).unlink(missing_ok=True)
    except OSError:
        pass

    session.pop("import_step", None)
    session.pop("import_ctx", None)
    session["import_result"] = stats
    return redirect(url_for("import_view"))


@app.route("/import/cancel", methods=["POST"])
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

# BIND_HOST is what the server listens on. "0.0.0.0" = every network interface,
# so other computers on the internal LAN can reach it at http://<server>:5000 —
# required for team hosting. Set NLC_BIND_HOST=127.0.0.1 to restrict the server
# to this machine only (the right choice for a personal copy on a laptop).
#
# There is NO sign-in: anything that can reach this port gets the whole
# dashboard, and the database holds client data. So the firewall must allow
# inbound TCP 5000 from the internal network ONLY — never expose this port to
# the public internet.
BIND_HOST = os.environ.get("NLC_BIND_HOST", "").strip() or "0.0.0.0"
# LOCAL_HOST is only for talking to ourselves — the "already running?" check and
# the auto-opened browser tab. A browser can't open http://0.0.0.0:5000, and you
# can't connect() to 0.0.0.0 as a client, so those always use the loopback.
LOCAL_HOST = "127.0.0.1"
PORT = 5000
URL = f"http://{LOCAL_HOST}:{PORT}"


def _server_already_running() -> bool:
    """True if something is already listening on our port (an earlier launch)."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((LOCAL_HOST, PORT)) == 0


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
        logging.info(f"Starting on http://{BIND_HOST}:{PORT} (Waitress) "
                     f"— local tab at {URL}")
        serve(app, host=BIND_HOST, port=PORT, threads=8,
              channel_timeout=60, cleanup_interval=30)
    except ImportError:
        logging.warning("Waitress not found — falling back to Flask dev server")
        app.run(host=BIND_HOST, port=PORT, debug=False, use_reloader=False)
