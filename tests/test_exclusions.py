import config

JPG = "Property Assignee"


def test_hidden_for_jpg_property_items():
    # Real examples from the data — all should be hidden for JPG.
    assert config.is_hidden_item(JPG, "Pardo Development Group INC", "16 Franklin Street")
    assert config.is_hidden_item(JPG, "Pardo Development Group INC", "104 Winding Way")
    assert config.is_hidden_item(JPG, "Pardo Development Group INC", "10 Whittier Place Newark")
    assert config.is_hidden_item(JPG, "Raritan 719 LLC", "2026 CAM Charges - 719 Tenant")
    assert config.is_hidden_item(JPG, "NFC Group LLC", "Deerfield Beach FL.")


def test_case_insensitive():
    assert config.is_hidden_item("property assignee", "X", "12 MAIN STREET")


def test_not_hidden_for_other_staff():
    # Same keyword, different assignee -> stays visible.
    assert not config.is_hidden_item("Dana Whitfield", "Some LLC", "16 Franklin Street")
    assert not config.is_hidden_item("Owen Bradfield", "X", "104 Winding Way")


def test_whole_word_only_no_false_hits():
    # 'way'/'place' inside other words must NOT trigger.
    assert not config.is_hidden_item(JPG, "Gateway Partners", "Annual audit")
    assert not config.is_hidden_item(JPG, "Someplace Holdings", "Driveway paving review")


def test_jpg_non_property_items_visible():
    assert not config.is_hidden_item(JPG, "Acme Corp", "2024 Individual Tax Return")


def test_former_staff_normalized_to_unassigned():
    for name in ("Clara Bexley", "Noor Rahimi", "Ivy Fenwick"):
        assert config.normalize_assignee(name) == "Unassigned"


def test_former_staff_case_insensitive_and_trimmed():
    assert config.normalize_assignee("  clara bexley  ") == "Unassigned"
    assert config.normalize_assignee("Noor Rahimi") == "Unassigned"


def test_other_staff_names_unchanged():
    assert config.normalize_assignee("Dana Whitfield") == "Dana Whitfield"
    assert config.normalize_assignee("(unassigned)") == "(unassigned)"
    assert config.normalize_assignee("") == ""
    assert config.normalize_assignee(None) is None
