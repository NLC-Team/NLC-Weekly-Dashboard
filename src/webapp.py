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
import sys
import tempfile
import threading
import webbrowser
from datetime import date
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
from flask import Flask, redirect, render_template, request, session, url_for
from matplotlib.figure import Figure

import config
from data import importer, service
from data.store import Store

app = Flask(__name__)
app.secret_key = "REDACTED-ROTATED-KEY"

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


def _current_user() -> str:
    return get_store().get_setting("current_user", "") or ""


def _current_role() -> str:
    """Role of whoever is currently 'acting as' in the app ("" if none)."""
    user = _current_user()
    if not user:
        return ""
    for m in get_store().get_staff():
        if m["name"] == user:
            return m["role"]
    return ""


def _is_admin() -> bool:
    return _current_role() == "Admin"


@app.context_processor
def _inject_user():
    """Make the current user/role and staff list available to every template."""
    return {
        "current_user": _current_user(),
        "current_role": _current_role(),
        "all_staff": get_store().get_staff(),
    }


def _get_data() -> dict:
    return service.dashboard_data(get_store(), date.today(), _overdue_days())


def _known_statuses() -> list[str]:
    rows = get_store().conn.execute(
        "SELECT DISTINCT last_status FROM items WHERE last_status != '' ORDER BY last_status"
    ).fetchall()
    return [r[0] for r in rows]


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

        # First name + last initial (e.g. "Sarah K.")
        def _short(name: str) -> str:
            parts = name.strip().split()
            if len(parts) >= 2:
                return f"{parts[0]} {parts[-1][0]}."
            return parts[0] if parts else name

        # Sort highest workload to smallest. Horizontal bars read top-to-bottom,
        # so reverse the list to put the busiest staff member at the top.
        ordered = sorted(top, key=lambda r: r["pending_count"], reverse=True)
        ordered = list(reversed(ordered))  # matplotlib draws y=0 at the bottom

        labels = [_short(r["assignee"]) for r in ordered]
        pending = [r["pending_count"] for r in ordered]
        overdue = [r["overdue_count"] for r in ordered]
        n = len(labels)
        y = list(range(n))
        h = 0.30  # bar thickness

        fig = Figure(figsize=(5.8, 3.4), facecolor="white")
        ax = fig.add_subplot(111)

        # Refined, professional palette: deep navy + restrained crimson.
        bars_p = ax.barh([yi + h / 2 for yi in y], pending,
                         height=h, label="Pending", color="#1e3a5f", zorder=3, linewidth=0)
        bars_o = ax.barh([yi - h / 2 for yi in y], overdue,
                         height=h, label="Overdue", color="#9d2235", zorder=3, linewidth=0)

        # Value labels at the end of each bar
        xmax = max(pending + overdue + [1])
        for bar in list(bars_p) + list(bars_o):
            w = bar.get_width()
            if w > 0:
                ax.text(w + xmax * 0.015, bar.get_y() + bar.get_height() / 2, str(int(w)),
                        ha="left", va="center", fontsize=10, color="#1f2937")

        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=11, color="#0f1923")
        ax.set_ylim(-0.6, n - 0.4)
        ax.set_xlim(0, xmax * 1.12)  # headroom for value labels

        # Classic, restrained styling to suit the serif typeface.
        ax.set_title("Staff Workload Summary", fontsize=16, color="#0f1923",
                     pad=14, loc="left")
        ax.spines[["top", "right", "bottom"]].set_visible(False)
        ax.spines["left"].set_color("#9ca3af")
        ax.spines["left"].set_linewidth(1.0)
        ax.tick_params(axis="both", colors="#1f2937", length=0)
        ax.xaxis.grid(True, color="#e5e7eb", linewidth=0.6, zorder=0)
        ax.set_axisbelow(True)
        ax.set_facecolor("white")
        ax.xaxis.set_visible(False)

        legend = ax.legend(fontsize=11, frameon=False, loc="lower right",
                           ncol=2, handlelength=1.1, handletextpad=0.5, columnspacing=1.0)

        fig.tight_layout(pad=1.2)
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

@app.route("/")
def overview():
    data = _get_data()
    li = get_store().last_import()
    age_dist = data["age_distribution"]
    max_age_count = max((d[1] for d in age_dist), default=1) or 1
    return render_template("overview.html",
                           data=data,
                           last_import=li,
                           overdue_days=_overdue_days(),
                           workload_img=_workload_chart(data["per_assignee"]),
                           age_dist=age_dist,
                           max_age_count=max_age_count)


@app.route("/person")
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
    counts = {"All": len(relevant)}
    for t in ("Individual", "Business", "Unclassified"):
        counts[t] = sum(1 for p in relevant if p["return_type"] == t)

    display = [p for p in relevant if ft == "All" or p["return_type"] == ft]
    if not pkey and display:
        pkey = display[0]["project_key"]

    selected = next((p for p in all_proj if p["project_key"] == pkey), None)
    totals = data["project_totals"]
    return render_template("projects.html",
                           display=display, selected=selected,
                           filter_type=ft, show_done=show_done,
                           counts=counts, totals=totals,
                           return_types=config.RETURN_TYPES)


@app.route("/projects/doc", methods=["POST"])
def project_doc():
    get_store().set_received(
        request.form["item_key"],
        request.form.get("received") == "1",
        date.today(),
    )
    return redirect(request.referrer or url_for("projects"))


@app.route("/projects/type", methods=["POST"])
def project_type():
    get_store().set_project_type(request.form["pkey"], request.form["return_type"])
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
    return redirect(url_for("projects",
                             filter=request.form.get("filter", "All"),
                             done=request.form.get("done", "0")))


@app.route("/overdue")
def overdue():
    data = _get_data()
    by_person: dict[str, list] = {}
    for it in data["overdue"]:
        by_person.setdefault(it["assignee"], []).append(it)
    ranked = sorted(by_person.items(), key=lambda kv: len(kv[1]), reverse=True)
    worst = {person: max(items, key=lambda x: x["age_days"])
             for person, items in ranked}
    return render_template("overdue.html",
                           overdue_items=data["overdue"],
                           overdue_days=_overdue_days(),
                           ranked=ranked, worst=worst)


@app.route("/whoami", methods=["POST"])
def whoami():
    """Set who is currently 'acting as' in the app (drives admin gating)."""
    get_store().set_setting("current_user", request.form.get("user", ""))
    return redirect(request.referrer or url_for("overview"))


@app.route("/clients")
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
def clients_delete():
    if not _is_admin():
        return redirect(url_for("clients"))
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
def staff():
    return render_template("staff.html",
                           members=get_store().get_staff(),
                           msg=request.args.get("msg", ""),
                           msg_type=request.args.get("mt", "ok"))


@app.route("/staff/add", methods=["POST"])
def staff_add():
    name = request.form.get("name", "").strip()
    role = request.form.get("role", "Viewer")
    if name:
        get_store().upsert_staff(name, role, date.today())
        return redirect(url_for("staff", msg=f"{name} added as {role}.", mt="ok"))
    return redirect(url_for("staff", msg="Please enter a name.", mt="err"))


@app.route("/staff/remove", methods=["POST"])
def staff_remove():
    name = request.form.get("name", "")
    if name:
        get_store().remove_staff(name)
    return redirect(url_for("staff"))


@app.route("/staff/role", methods=["POST"])
def staff_role():
    name = request.form.get("name", "")
    role = request.form.get("role", "Viewer")
    if name:
        get_store().upsert_staff(name, role, date.today())
        return redirect(url_for("staff", msg=f"{name} updated to {role}.", mt="ok"))
    return redirect(url_for("staff"))


@app.route("/settings")
def settings():
    return render_template("settings.html",
                           overdue_days=_overdue_days(),
                           pending_statuses=_pending_statuses(),
                           known_statuses=_known_statuses(),
                           db_path=str(get_store().db_path),
                           msg=request.args.get("msg", ""))


@app.route("/settings/days", methods=["POST"])
def settings_days():
    try:
        days = max(1, int(request.form["days"]))
    except (ValueError, KeyError):
        days = _overdue_days()
    get_store().set_setting("overdue_days", days)
    return redirect(url_for("settings", msg=f"Overdue threshold set to {days} days."))


@app.route("/settings/statuses", methods=["POST"])
def settings_statuses():
    statuses = request.form.getlist("statuses")
    get_store().set_setting("pending_statuses", statuses)
    return redirect(url_for("settings", msg="Pending statuses saved."))


# ---- Import flow ---------------------------------------------------------

@app.route("/import", methods=["GET"])
def import_view():
    step = session.get("import_step", 0)
    ctx = session.get("import_ctx", {})
    result = session.pop("import_result", None)
    return render_template("import.html", step=step, ctx=ctx,
                           result=result,
                           logical_fields=config.LOGICAL_FIELDS)


@app.route("/import/upload", methods=["POST"])
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
