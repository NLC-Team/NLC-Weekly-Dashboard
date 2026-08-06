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


def test_internal_and_test_clients_excluded():
    assert config.is_excluded_client("Anders, Jamie")
    assert config.is_excluded_client("NLC Financial Services, LLC (Internal)")
    assert config.is_excluded_client("Test, Sample")


def test_nlc_training_contact_excluded():
    assert config.is_excluded_client("Example Traning CONTACT")


def test_nlc_internal_excluded_with_or_without_the_comma():
    # Karbon has exported this name both ways; a missing comma must not put 121
    # internal documents back into the dashboard/PDF/Excel.
    assert config.is_excluded_client("NLC Financial Services LLC (Internal)")


def test_excluded_match_ignores_case_spacing_and_punctuation():
    assert config.is_excluded_client("  nlc financial   services,, LLC (INTERNAL) ")
    assert config.is_excluded_client("TEST, SAMPLE")
    assert config.is_excluded_client("Anders, Jamie")


def test_similar_real_nlc_clients_are_not_excluded():
    # Near-misses that must keep counting: punctuation-insensitive matching must
    # not swallow these.
    assert not config.is_excluded_client("NLC Financial Services, LLC - Alan")
    assert not config.is_excluded_client("NLC Assurance Group LLC")
    assert not config.is_excluded_client("NLC Financial Services, LLC")


def test_blank_client_is_not_excluded():
    assert not config.is_excluded_client("")
    assert not config.is_excluded_client(None)


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
