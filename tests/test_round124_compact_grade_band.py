"""Round 124 / F3 + F4 regression tests.

F3: Compact portfolio health-grade grounding + stamp (recognise the Compact
    ``Portfolio Health:`` label, overwrite the letter with the canonical
    band-distribution grade, render the canonical-risk-bands briefing block).
F4: Compact per-customer ``Risk: [BAND]`` line reconciliation against the
    canonical band from ``compute_customer_risk_profile``.
"""

import adoptiq_backend as ab


# ---------------------------------------------------------------------------
# F3 -- portfolio health-grade label alias + stamp
# ---------------------------------------------------------------------------
def test_portfolio_regex_matches_compact_label():
    # Compact prompt emits "Portfolio Health:" (no " Score").
    assert ab.extract_portfolio_health_grade("## **Portfolio Health: F**") == "F"


def test_portfolio_regex_matches_comprehensive_label():
    # Comprehensive prompt emits "Portfolio Health Score:".
    assert ab.extract_portfolio_health_grade("## **Portfolio Health Score: B**") == "B"


def test_stamp_overwrites_compact_portfolio_grade():
    text = "## **Portfolio Health: F**\nThe portfolio is in crisis."
    stamped = ab.stamp_portfolio_health_grade(text, "A")
    assert ab.extract_portfolio_health_grade(stamped) == "A"
    # Label wording + bold chrome preserved.
    assert "Portfolio Health:" in stamped
    assert "**Portfolio Health: A**" in stamped


def test_stamp_overwrites_comprehensive_portfolio_grade():
    text = "Portfolio Health Score: C"
    assert ab.extract_portfolio_health_grade(ab.stamp_portfolio_health_grade(text, "B")) == "B"


def test_stamp_portfolio_grade_idempotent():
    text = "Portfolio Health: D"
    once = ab.stamp_portfolio_health_grade(text, "A")
    twice = ab.stamp_portfolio_health_grade(once, "A")
    assert once == twice


def test_stamp_skips_out_of_range_letter():
    text = "Portfolio Health: F"
    # Canonical letter "G" is not A-F -> never stamp garbage.
    assert ab.stamp_portfolio_health_grade(text, "G") == text


# ---------------------------------------------------------------------------
# F4 -- per-customer band reconciliation
# ---------------------------------------------------------------------------
def test_compact_band_rewrites_critical_to_canonical_high():
    narrative = (
        "## **Customers in Trouble**\n\n"
        "**1. FARMERS INSURANCE - Risk: CRITICAL**\n"
        "- Problem: stalled adoption\n"
        "**2. NATIONAL GRID - Risk: CRITICAL**\n"
        "- Problem: open barriers\n"
    )
    bands = {"FARMERS INSURANCE": "HIGH", "NATIONAL GRID": "HIGH"}
    out = ab.stamp_compact_customer_bands(narrative, bands)
    assert "**1. FARMERS INSURANCE - Risk: HIGH**" in out
    assert "**2. NATIONAL GRID - Risk: HIGH**" in out
    assert "CRITICAL" not in out


def test_compact_band_preserves_index_name_and_bold():
    narrative = "**3. ACME CORP - Risk: HIGH**"
    out = ab.stamp_compact_customer_bands(narrative, {"ACME CORP": "MEDIUM"})
    assert out == "**3. ACME CORP - Risk: MEDIUM**"


def test_compact_band_unknown_customer_left_unchanged():
    narrative = "**1. MYSTERY INC - Risk: CRITICAL**"
    out = ab.stamp_compact_customer_bands(narrative, {"FARMERS INSURANCE": "HIGH"})
    assert out == narrative  # not in canonical map -> leave LLM band


def test_compact_band_matches_punctuation_variants():
    # Alnum-only key collapses punctuation / case differences.
    narrative = "**1. Farmers Insurance - Risk: CRITICAL**"
    out = ab.stamp_compact_customer_bands(narrative, {"FARMERS INSURANCE": "HIGH"})
    assert "Risk: HIGH" in out


def test_compact_band_tolerates_en_dash_separator():
    narrative = "**1. ACME - Risk: CRITICAL**".replace("-", "\u2013")
    out = ab.stamp_compact_customer_bands(narrative, {"ACME": "LOW"})
    assert "Risk: LOW" in out


def test_compact_band_no_per_customer_line_unchanged():
    narrative = "Just some prose with no risk lines at all."
    assert ab.stamp_compact_customer_bands(narrative, {"ACME": "HIGH"}) == narrative


def test_compact_band_empty_map_unchanged():
    narrative = "**1. ACME - Risk: CRITICAL**"
    assert ab.stamp_compact_customer_bands(narrative, {}) == narrative


def test_compact_band_idempotent():
    narrative = "**1. ACME - Risk: CRITICAL**"
    bands = {"ACME": "HIGH"}
    once = ab.stamp_compact_customer_bands(narrative, bands)
    twice = ab.stamp_compact_customer_bands(once, bands)
    assert once == twice


def test_compact_band_ignores_off_vocab_band_in_map():
    # A non-vocab canonical band is dropped from the lookup -> no rewrite.
    narrative = "**1. ACME - Risk: CRITICAL**"
    out = ab.stamp_compact_customer_bands(narrative, {"ACME": "WEIRD"})
    assert out == narrative


# ---------------------------------------------------------------------------
# F3 -- canonical-risk-bands briefing block renderer (in app_simple)
# ---------------------------------------------------------------------------
def test_render_canonical_risk_bands_block():
    import app_simple as app

    risk_scores = {
        "FARMERS INSURANCE": {"risk_band": "HIGH", "risk_score_0_100": 60.0},
        "ACME CORP": {"risk_band": "LOW", "risk_score_0_100": 20.0},
    }
    from risk_scoring import compute_portfolio_risk_summary, portfolio_health_grade

    summary = compute_portfolio_risk_summary(risk_scores)
    grade = portfolio_health_grade(summary)
    block = app._r124_render_canonical_risk_bands(risk_scores, summary, grade)
    assert "Canonical Risk Bands" in block
    assert "Portfolio Health:" in block
    # Highest score first (deterministic ordering).
    assert block.index("FARMERS INSURANCE") < block.index("ACME CORP")
    assert "HIGH" in block and "LOW" in block


def test_render_canonical_risk_bands_empty_returns_blank():
    import app_simple as app

    assert app._r124_render_canonical_risk_bands({}, {}, "A") == ""
    assert app._r124_render_canonical_risk_bands(None, None, None) == ""
