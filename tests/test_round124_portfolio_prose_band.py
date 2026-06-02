"""Round 124 / F6: Comprehensive portfolio grade-vs-prose reconciliation.

Build 92 stamped the Portfolio Health Score letter correctly (B = LOW) but the
LLM justification prose still asserted a "MEDIUM risk state" and trailed off
with a stray "(Rate not available).".  These pure helpers align the prose band
word with the stamped letter and strip the leftover artifact.

Made-with: Cursor.
"""

import adoptiq_backend as ab


# ---------------------------------------------------------------------------
# reconcile_portfolio_prose_band
# ---------------------------------------------------------------------------


def test_prose_band_rewrites_medium_to_low_for_grade_b():
    text = "The portfolio is in a MEDIUM risk state overall."
    out = ab.reconcile_portfolio_prose_band(text, "B")
    assert "LOW risk state" in out
    assert "MEDIUM risk state" not in out


def test_prose_band_rewrites_posture_idiom():
    text = "Customers reflect a HIGH risk posture this quarter."
    out = ab.reconcile_portfolio_prose_band(text, "A")
    assert "HEALTHY risk posture" in out


def test_prose_band_preserves_bare_band_counts():
    # "HIGH risk customers" is a count phrase, not the state/posture idiom.
    text = "There are 4 HIGH risk customers in the book."
    out = ab.reconcile_portfolio_prose_band(text, "B")
    assert out == text


def test_prose_band_unknown_letter_is_noop():
    text = "The portfolio is in a MEDIUM risk state."
    assert ab.reconcile_portfolio_prose_band(text, "Z") == text
    assert ab.reconcile_portfolio_prose_band(text, "") == text


def test_prose_band_idempotent():
    text = "The portfolio is in a MEDIUM risk state."
    once = ab.reconcile_portfolio_prose_band(text, "B")
    twice = ab.reconcile_portfolio_prose_band(once, "B")
    assert once == twice


def test_prose_band_non_string_is_safe():
    assert ab.reconcile_portfolio_prose_band(None, "B") is None
    assert ab.reconcile_portfolio_prose_band("", "B") == ""


# ---------------------------------------------------------------------------
# strip_rate_not_available
# ---------------------------------------------------------------------------


def test_strip_rate_not_available_removes_artifact():
    text = "Adoption is improving (Rate not available)."
    out = ab.strip_rate_not_available(text)
    assert "Rate not available" not in out
    assert out.strip() == "Adoption is improving"


def test_strip_rate_not_available_case_insensitive_no_trailing_period():
    text = "Cases trending down (rate not available)"
    out = ab.strip_rate_not_available(text)
    assert "rate not available" not in out.lower()


def test_strip_rate_not_available_noop_when_absent():
    text = "A clean portfolio narrative with no artifact."
    assert ab.strip_rate_not_available(text) == text


def test_strip_rate_not_available_non_string_is_safe():
    assert ab.strip_rate_not_available(None) is None
    assert ab.strip_rate_not_available("") == ""
