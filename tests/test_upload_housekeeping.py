"""Abandoned import uploads get swept.

The wizard deletes its staged file when the import runs or is cancelled, but a
wizard that is simply abandoned (tab closed, session lost) leaves the file with
nothing to delete it. Those files are real Karbon exports — client data — so
they must not accumulate forever, which on one real installation they had: three
files up to three weeks old.
"""
import os
import time

import config
import webapp


def _staged(dirpath, name, age_days):
    p = dirpath / name
    p.write_bytes(b"pretend Karbon export")
    old = time.time() - age_days * 86400
    os.utime(p, (old, old))
    return p


def test_sweeps_only_files_past_the_ttl(tmp_path, monkeypatch):
    up = tmp_path / "import_uploads"
    up.mkdir()
    monkeypatch.setattr(config, "default_import_upload_dir", lambda: up)

    stale_a = _staged(up, "stale_a.xlsx", 21)
    stale_b = _staged(up, "stale_b.csv", 8)
    fresh = _staged(up, "fresh.xlsx", 1)          # inside the window
    edge = _staged(up, "edge.xlsx", 6)            # also inside

    removed = webapp.sweep_stale_uploads(ttl_days=7)

    assert removed == 2
    assert not stale_a.exists()
    assert not stale_b.exists()
    assert fresh.exists(), "a recent upload must survive — a wizard may be mid-flight"
    assert edge.exists()


def test_sweep_is_a_no_op_on_an_empty_folder(tmp_path, monkeypatch):
    up = tmp_path / "import_uploads"
    up.mkdir()
    monkeypatch.setattr(config, "default_import_upload_dir", lambda: up)
    assert webapp.sweep_stale_uploads() == 0


def test_sweep_survives_a_missing_folder(tmp_path, monkeypatch):
    """Housekeeping must never stop the dashboard from starting."""
    monkeypatch.setattr(config, "default_import_upload_dir",
                        lambda: tmp_path / "does-not-exist")
    assert webapp.sweep_stale_uploads() == 0


def test_sweep_ignores_subdirectories(tmp_path, monkeypatch):
    up = tmp_path / "import_uploads"
    up.mkdir()
    sub = up / "somedir"
    sub.mkdir()
    old = time.time() - 30 * 86400
    os.utime(sub, (old, old))
    monkeypatch.setattr(config, "default_import_upload_dir", lambda: up)
    assert webapp.sweep_stale_uploads() == 0
    assert sub.exists()


def test_default_ttl_is_a_week():
    assert webapp.UPLOAD_TTL_DAYS == 7
