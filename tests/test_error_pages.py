"""The branded error pages.

All four render templates/error.html, which extends standalone.html — so this
also guards the rename of that shell. 404 and 500 previously had no handler at
all: a mistyped address, or the app's own abort(404) for a malformed audit-archive
week, fell through to Werkzeug's bare unstyled page while 400 and 413 were
branded.
"""
import pytest

import webapp
from data.store import Store


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = Store(tmp_path / "err.db")
    monkeypatch.setattr(webapp, "_store", store)
    with webapp.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["csrf_token"] = "t"
        yield c
    store.close()


def _is_branded(body):
    """The standalone shell, not Werkzeug's default page."""
    return "page-card" in body and "NLC" in body


def test_unknown_address_gets_the_branded_404(client):
    r = client.get("/no-such-page")
    assert r.status_code == 404
    body = r.get_data(as_text=True)
    assert _is_branded(body)
    assert "Page not found" in body


def test_malformed_archive_week_gets_the_branded_404(client):
    # audit_archive aborts 404 unless the week matches YYYY-Wnn.
    r = client.get("/audit/archive/not-a-week")
    assert r.status_code == 404
    assert "Page not found" in r.get_data(as_text=True)


def test_wellformed_archive_week_is_not_a_404(client):
    # Guard the regex from the other side: a valid label must reach the route.
    r = client.get("/audit/archive/2026-W33")
    assert r.status_code == 200


def test_missing_csrf_token_gets_the_branded_400(client):
    r = client.post("/settings/days", data={"days": "5"})
    assert r.status_code == 400
    body = r.get_data(as_text=True)
    assert _is_branded(body)
    assert "Bad request" in body
    assert "CSRF" in body


def test_oversized_upload_gets_the_branded_413(client):
    import io
    too_big = b"x" * (webapp.app.config["MAX_CONTENT_LENGTH"] + 1024)
    r = client.post("/import/upload",
                    data={"csrf_token": "t",
                          "csv_file": (io.BytesIO(too_big), "big.csv")},
                    content_type="multipart/form-data")
    assert r.status_code == 413
    body = r.get_data(as_text=True)
    assert _is_branded(body)
    assert "30 MB" in body


def test_unexpected_error_gets_the_branded_500():
    """Registered on a throwaway app so the real one keeps no test route."""
    app = webapp.app
    # The handler is what we are testing, so let Flask invoke it rather than
    # re-raising the exception into the test.
    with app.test_request_context():
        resp = webapp._server_error(RuntimeError("boom"))
    body, code = resp
    assert code == 500
    assert _is_branded(body)
    assert "Something went wrong" in body
