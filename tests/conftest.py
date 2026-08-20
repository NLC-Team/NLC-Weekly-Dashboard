import os
import sys

import pytest

# Make the `src` package importable as top-level (data.*, config, ui.*).
SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import config  # noqa: E402  (must follow the sys.path setup above)


@pytest.fixture(autouse=True)
def neutral_firm_rules(monkeypatch):
    """Run every test as if there were no local_config.py.

    The firm-specific rules (hidden property items, excluded clients, former
    staff, the Weekly Review's owner filter) live in an untracked
    `src/local_config.py`. If tests inherited whatever that file happens to say,
    the suite would pass or fail depending on the machine it runs on -- and it
    would not run at all the same way for someone who just cloned the repo.

    So: reset all of them to their "no local config" defaults here, and let the
    handful of tests that actually exercise these rules set their own values.
    """
    monkeypatch.setattr(config, "HIDDEN_ITEM_ASSIGNEE", "")
    monkeypatch.setattr(config, "HIDDEN_ITEM_WORDS", ())
    monkeypatch.setattr(config, "EXCLUDED_CLIENT_NAMES", set())
    monkeypatch.setattr(config, "UNASSIGNED_STAFF_NAMES", set())
    monkeypatch.setattr(config, "REVIEW_OWNER_PREFIX", "")
