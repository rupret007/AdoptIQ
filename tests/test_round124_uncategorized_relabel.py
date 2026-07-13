"""Round 124 / F8: "uncategorized" theme sentinel relabel.

``adoptiq_backend._normalize_category`` can emit ``"Uncategorized"`` for AB
rows with no recognized theme.  Pre-R124 ``_R121_UNCLASSIFIED_TOKENS`` did not
carry that token so the BE Focus Areas Theme column displayed a bare
"Uncategorized" instead of the canonical "Other / Unclassified" label that the
Technology column already used.

Made-with: Cursor.
"""

import be_priority_scorer as bes


def test_uncategorized_token_present():
    assert "uncategorized" in bes._R121_UNCLASSIFIED_TOKENS


def test_uncategorized_relabels_to_other_unclassified():
    for sentinel in ["Uncategorized", "uncategorized", "  Uncategorized  "]:
        assert bes.relabel_unclassified(sentinel) == "Other / Unclassified", sentinel


def test_genuine_label_with_uncategorized_substring_preserved():
    # Only an exact sentinel match relabels; a substring keeps the real label.
    assert bes.relabel_unclassified("Uncategorized Webex Feature") == (
        "Uncategorized Webex Feature"
    )
