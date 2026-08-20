"""The three firm-specific filters in config.py: hidden property line-items,
excluded internal/test clients, and former staff relabelled "Unassigned".

These tests deliberately use INVENTED names. The real values live in an
untracked local_config.py (see local_config.example.py), so the fixture below
installs a known synthetic set and the tests exercise the matching *logic* —
whole-word matching, punctuation-insensitive comparison, the single-assignee
scope — rather than whichever names a given installation happens to have.
"""
import pytest

import config

PROPERTY_STAFF = "Robin Property Keeper"
FORMER_STAFF = ("dana gone", "lee departed")


@pytest.fixture(autouse=True)
def firm_rules(monkeypatch):
    """Point config at a synthetic rule set for every test in this module."""
    monkeypatch.setattr(config, "HIDDEN_ITEM_ASSIGNEE", PROPERTY_STAFF)
    monkeypatch.setattr(config, "HIDDEN_ITEM_WORDS",
                        ("street", "deerfield", "way", "place", "tenant"))
    monkeypatch.setattr(config, "EXCLUDED_CLIENT_NAMES", {
        "Anders, Jamie",
        "Example Financial Services, LLC (Internal)",
        "Example Traning CONTACT",
        "Test, Sample",
    })
    monkeypatch.setattr(config, "UNASSIGNED_STAFF_NAMES", set(FORMER_STAFF))


# ---- Hidden property line-items -------------------------------------------

def test_property_items_are_hidden_for_that_one_assignee():
    assert config.is_hidden_item(PROPERTY_STAFF, "Pardo Development Group INC",
                                 "16 Franklin Street")
    assert config.is_hidden_item(PROPERTY_STAFF, "Pardo Development Group INC",
                                 "104 Winding Way")
    assert config.is_hidden_item(PROPERTY_STAFF, "Pardo Development Group INC",
                                 "10 Whittier Place Newark")
    assert config.is_hidden_item(PROPERTY_STAFF, "Raritan 719 LLC",
                                 "2026 CAM Charges - 719 Tenant")
    # The keyword may appear in the CLIENT name rather than the title.
    assert config.is_hidden_item(PROPERTY_STAFF, "Deerfield Holdings", "Annual review")


def test_assignee_match_is_case_insensitive():
    assert config.is_hidden_item(PROPERTY_STAFF.lower(), "X", "12 MAIN STREET")


def test_not_hidden_for_other_staff():
    # Same keyword, different assignee -> stays visible. This is the whole point
    # of the rule being scoped to one person.
    assert not config.is_hidden_item("Someone Else", "Some LLC", "16 Franklin Street")
    assert not config.is_hidden_item("Another Person", "X", "104 Winding Way")


def test_whole_word_only_no_false_hits():
    # 'way'/'place' inside other words must NOT trigger.
    assert not config.is_hidden_item(PROPERTY_STAFF, "Gateway Partners", "Annual audit")
    assert not config.is_hidden_item(PROPERTY_STAFF, "Someplace Holdings",
                                     "Driveway paving review")


def test_non_property_work_stays_visible_even_for_that_assignee():
    assert not config.is_hidden_item(PROPERTY_STAFF, "Acme Corp",
                                     "2024 Individual Tax Return")


def test_rule_is_off_when_no_assignee_is_configured(monkeypatch):
    """With no local_config.py the rule must be inert -- and in particular an
    empty word list must not compile to a pattern that matches everything."""
    monkeypatch.setattr(config, "HIDDEN_ITEM_ASSIGNEE", "")
    monkeypatch.setattr(config, "HIDDEN_ITEM_WORDS", ())
    assert not config.is_hidden_item("", "Anything", "16 Franklin Street")
    assert not config.is_hidden_item(PROPERTY_STAFF, "Anything", "16 Franklin Street")


def test_empty_word_list_hides_nothing(monkeypatch):
    monkeypatch.setattr(config, "HIDDEN_ITEM_WORDS", ())
    assert not config.is_hidden_item(PROPERTY_STAFF, "Deerfield Holdings",
                                     "16 Franklin Street")


# ---- Excluded clients ------------------------------------------------------

def test_internal_and_test_clients_excluded():
    assert config.is_excluded_client("Anders, Jamie")
    assert config.is_excluded_client("Example Financial Services, LLC (Internal)")
    assert config.is_excluded_client("Test, Sample")
    assert config.is_excluded_client("Example Traning CONTACT")


def test_excluded_with_or_without_the_comma():
    # Karbon has exported the same name both ways; a missing comma must not put
    # a pile of internal documents back into the dashboard/PDF/Excel.
    assert config.is_excluded_client("Example Financial Services LLC (Internal)")


def test_excluded_match_ignores_case_spacing_and_punctuation():
    assert config.is_excluded_client("  example financial   services,, LLC (INTERNAL) ")
    assert config.is_excluded_client("TEST, SAMPLE")
    assert config.is_excluded_client("Anders Jamie")


def test_similar_client_names_are_not_excluded():
    # Near-misses that must keep counting: punctuation-insensitive matching must
    # not swallow these.
    assert not config.is_excluded_client("Example Financial Services, LLC - Alan")
    assert not config.is_excluded_client("Example Assurance Group LLC")
    assert not config.is_excluded_client("Example Financial Services, LLC")


def test_blank_client_is_not_excluded():
    assert not config.is_excluded_client("")
    assert not config.is_excluded_client(None)


def test_nothing_excluded_when_the_list_is_empty(monkeypatch):
    monkeypatch.setattr(config, "EXCLUDED_CLIENT_NAMES", set())
    assert not config.is_excluded_client("Anders, Jamie")
    assert not config.is_excluded_client("")


# ---- Former staff ----------------------------------------------------------

def test_former_staff_normalized_to_unassigned():
    for name in ("Dana Gone", "Lee Departed"):
        assert config.normalize_assignee(name) == "Unassigned"


def test_former_staff_case_insensitive_and_trimmed():
    assert config.normalize_assignee("  dana gone  ") == "Unassigned"
    assert config.normalize_assignee("LEE DEPARTED") == "Unassigned"


def test_other_staff_names_unchanged():
    assert config.normalize_assignee("Current Person") == "Current Person"
    assert config.normalize_assignee("(unassigned)") == "(unassigned)"
    assert config.normalize_assignee("") == ""
    assert config.normalize_assignee(None) is None


def test_no_relabelling_when_the_list_is_empty(monkeypatch):
    monkeypatch.setattr(config, "UNASSIGNED_STAFF_NAMES", set())
    assert config.normalize_assignee("Dana Gone") == "Dana Gone"
