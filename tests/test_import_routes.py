"""Regression tests for the import wizard's failure modes (see conversation:
users were hitting silent resets with zero explanation -- "click upload and it
just reloads the same page" / "click Run import and it just goes back to
upload"). Root causes: (1) no file selected -> silent redirect, no message;
(2) the uploaded file's tmp path went stale (e.g. app restart) -> silent
session wipe back to step 0, no message; (3) an exception during the actual
import -> unhandled, blank error page, nothing logged anywhere (pythonw.exe
has no console). All three now show a clear on-page message instead."""
import pytest

import config
import webapp
from data.store import Store


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = Store(tmp_path / "test.db")
    monkeypatch.setattr(webapp, "_store", store)
    # Route uploads to an isolated temp dir instead of the real app-data one.
    upload_dir = tmp_path / "import_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "default_import_upload_dir", lambda: upload_dir)
    webapp.app.testing = True
    with webapp.app.test_client() as c:
        # There is no login, but POSTs still have to carry the CSRF token the
        # session holds (see webapp._csrf_gate).
        with c.session_transaction() as sess:
            sess["csrf_token"] = "test-token"
        yield c
    store.close()


def _csv_bytes():
    return (
        b"Client Name,Assignee,Work Title,Status,Start Date,Due Date\n"
        b"Acme,Sarah,W-2,In Progress,2026-06-01,2026-08-01\n"
        b"Beta,James,1099,Completed,2026-05-15,2026-07-01\n"
    )


def test_upload_with_no_file_shows_clear_error(client):
    r = client.post("/import/upload", data={"csrf_token": "test-token"},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    assert b"No file was selected" in r.data


def test_run_with_stale_tmp_path_shows_clear_error(client):
    # Simulate the wizard reaching step 2, then the uploaded file going missing
    # (e.g. a server restart between "Upload" and "Run import").
    with client.session_transaction() as sess:
        sess["import_step"] = 1
        sess["import_ctx"] = {"tmp_path": "/nonexistent/path/gone.xlsx",
                              "filename": "gone.xlsx", "columns": [], "guesses": {},
                              "all_statuses": [], "row_count": 0, "sig": "x"}
    r = client.post("/import/run", data={"csrf_token": "test-token"})
    assert r.status_code == 200
    assert b"no longer available" in r.data
    with client.session_transaction() as sess:
        assert "import_ctx" not in sess  # stale state is cleared, not left dangling


def test_full_upload_and_run_happy_path(client):
    upload = client.post(
        "/import/upload",
        data={"csrf_token": "test-token",
              "csv_file": (io_bytes := __import__("io").BytesIO(_csv_bytes()), "test.csv")},
        content_type="multipart/form-data",
    )
    assert upload.status_code in (200, 302)
    with client.session_transaction() as sess:
        assert sess.get("import_step") == 1
        ctx = sess["import_ctx"]
    assert ctx["guesses"]["due_date"] == "Due Date"  # picked the right column, not "date"
    assert ctx["guesses"]["date"] == "Start Date"

    form = {"csrf_token": "test-token", "completed_status": "Completed"}
    for field, col in ctx["guesses"].items():
        form[f"map_{field}"] = col
    run = client.post("/import/run", data=form)
    assert run.status_code == 302
    result_page = client.get("/import")
    assert b"Import complete" in result_page.data
    assert b"2" in result_page.data  # 2 rows imported


def test_import_run_exception_shows_error_and_keeps_mapping(client, monkeypatch):
    with client.session_transaction() as sess:
        sess["import_step"] = 1
        sess["import_ctx"] = {"tmp_path": str(config.default_import_upload_dir() / "x.csv"),
                              "filename": "boom.csv", "columns": ["A"], "guesses": {},
                              "all_statuses": [], "row_count": 1, "sig": "x"}
    (config.default_import_upload_dir()).mkdir(parents=True, exist_ok=True)
    (config.default_import_upload_dir() / "x.csv").write_text("A\n1\n")

    def _boom(*a, **kw):
        raise RuntimeError("simulated import failure")
    monkeypatch.setattr(webapp.service, "import_csv", _boom)

    r = client.post("/import/run", data={"csrf_token": "test-token"})
    assert r.status_code == 200
    assert b"Something went wrong" in r.data
    assert b"boom.csv" in r.data  # ctx (mapping progress) survived the failure
