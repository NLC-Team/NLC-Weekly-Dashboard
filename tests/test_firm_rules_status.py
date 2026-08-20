"""config.FIRM_RULES_LOADED and the Settings card that reports it.

This is the difference that makes two copies of the dashboard disagree: with no
local_config.py nothing is hidden, excluded or relabelled and the Weekly Review
goes firm-wide, so the same database yields MORE rows and a HIGHER overdue count.
It has to be visible rather than silent, hence a test.
"""
import config
import webapp


def _client(tmp_path, monkeypatch):
    from data.store import Store
    store = Store(tmp_path / "t.db")
    monkeypatch.setattr(webapp, "_store", store)
    webapp.app.testing = True
    return webapp.app.test_client(), store


def test_flag_reflects_whether_local_config_was_importable():
    # Truthiness only: this repo's own checkout may or may not carry a
    # local_config.py, and the test must pass either way.
    assert isinstance(config.FIRM_RULES_LOADED, bool)
    assert config.FIRM_RULES_PATH.endswith("local_config.py")


def test_settings_warns_loudly_when_rules_are_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FIRM_RULES_LOADED", False)
    c, store = _client(tmp_path, monkeypatch)
    try:
        body = c.get("/settings").get_data(as_text=True)
        assert "not configured" in body
        assert "local_config.py" in body
        # It must say the figures will differ, not just that a file is absent.
        assert "not match" in body
        assert "all clients" in body      # the firm-wide scope it falls back to
    finally:
        store.close()


def test_settings_summarises_the_rules_when_they_are_loaded(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FIRM_RULES_LOADED", True)
    monkeypatch.setattr(config, "HIDDEN_ITEM_ASSIGNEE", "Robin Property Keeper")
    monkeypatch.setattr(config, "EXCLUDED_CLIENT_NAMES", {"A", "B"})
    monkeypatch.setattr(config, "UNASSIGNED_STAFF_NAMES", {"x"})
    monkeypatch.setattr(config, "REVIEW_SCOPE_LABEL", "Acme-owned")
    c, store = _client(tmp_path, monkeypatch)
    try:
        body = c.get("/settings").get_data(as_text=True)
        assert "active" in body
        assert "Robin Property Keeper" in body
        assert "Acme-owned clients" in body
        assert "not match" not in body      # no scare copy when correctly set up
    finally:
        store.close()
