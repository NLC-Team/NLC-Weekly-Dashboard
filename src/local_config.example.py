"""Firm-specific rules — TEMPLATE. Copy to `local_config.py` and edit.

`local_config.py` is git-ignored on purpose: these settings name real employees
and real clients, and this repository is public. Nothing here is required — with
no `local_config.py` at all the dashboard runs fine and simply applies none of
these rules.

    copy local_config.example.py local_config.py     (Windows)
    cp   local_config.example.py local_config.py     (macOS / Linux)

Everything is read once at import, so restart the dashboard after editing.
"""

# --- The firm's display name ------------------------------------------------
# Heads the Weekly Review page, its PDF, and the Excel export.
FIRM_NAME = "Your Firm Name"

# --- Weekly Review scope ----------------------------------------------------
# The Weekly Review can be narrowed to ONE partner's book of business: it then
# covers clients whose Karbon "Client Owner" starts with this prefix, plus
# clients with no owner recorded at all (a blank field is included rather than
# silently dropped, so nothing falls out of the review just because Karbon's
# Client Owner column was left empty).
#
# Matched as a lower-cased prefix, so a first name is enough ("dana" matches
# "Dana Whitfield"). Leave it "" to apply no owner filtering — every client is
# then in scope, which is what you want if the review should cover the firm.
REVIEW_OWNER_PREFIX = ""

# --- Hidden property line-items --------------------------------------------
# If ONE staff member tracks real-estate/property line items that aren't tax
# statements, name them here and the dashboard will hide those items everywhere.
#
# An item is treated as a property when its client name or work title contains
# any HIDDEN_ITEM_WORDS entry as a whole word, case-insensitively — e.g. "16
# Franklin Street", "104 Winding Way", "719 Tenant".
#
# Only this one assignee is affected: the same words on anyone else's work are
# left completely alone. Leave HIDDEN_ITEM_ASSIGNEE as "" to disable the rule.
HIDDEN_ITEM_ASSIGNEE = ""
HIDDEN_ITEM_WORDS = ("street", "way", "place", "tenant")

# --- Excluded clients -------------------------------------------------------
# Internal or test clients that should never appear anywhere — not on any page,
# not in the PDF, not in the Excel export.
#
# Write each name as it reads in Karbon. Matching normalises both sides, so
# case, stray whitespace and commas/periods don't matter: "Test, Sample",
# "Test, Sample" and "  test,, Owen " are all the same entry. The words themselves
# must still match, so a client that merely shares a prefix ("Acme Holdings LLC
# - Alan") is NOT excluded by an entry for "Acme Holdings LLC".
EXCLUDED_CLIENT_NAMES = {
    "Test, Example",
    "Your Firm Name, LLC (Internal)",
}

# --- Former staff ----------------------------------------------------------
# People who have left, but whose remaining documents should still be counted
# everywhere — just shown as "Unassigned" instead of under their own name.
#
# This is preferred over deleting or reassigning their work: the documents stay
# in the totals, they simply stop being attributed to someone who has gone.
# Matched case-insensitively and trimmed.
UNASSIGNED_STAFF_NAMES = {
    # "jane doe",
}
