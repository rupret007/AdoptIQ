"""Round 125 / Build 94 -- report-accuracy reconciler regression tests.

Pins the deterministic post-LLM reconcilers introduced/extended in
Round 125:

* ``ensure_portfolio_health_grade_line`` (A5) -- guarantees the
  ``Portfolio Health Score: X`` line is present even when the LLM omits
  it (the All-Managers Comprehensive case).
* ``reconcile_portfolio_prose_band`` (A2) -- the widened descriptor noun
  set now collapses "MEDIUM risk state" to the canonical grade-implied
  band word.
* ``_R78_STUB_RE`` (A4) -- the ``Pattern N: Data Unavailable [Source:..]``
  stub shape is matched so it can be dropped before write.
* ``reconcile_avg_risk_claim`` (B2) -- ungrounded "average risk score is
  26.4" rewritten to the canonical ``X.X/10``.
* ``reconcile_p2_active_claim`` (B6) -- "5 active P2 cases" rewritten to
  the canonical deduped count.

Each reconciler MUST be pure + idempotent.
"""

import adoptiq_backend as ab


# --------------------------------------------------------------------------
# A5: ensure_portfolio_health_grade_line
# --------------------------------------------------------------------------

def test_ensure_grade_line_inserts_when_absent():
    narrative = "Portfolio Overview\n\nThe portfolio is in good shape overall."
    out = ab.ensure_portfolio_health_grade_line(narrative, "B")
    assert "Portfolio Health Score: B" in out
    # Original content preserved.
    assert "good shape overall" in out


def test_ensure_grade_line_stamps_existing_to_canonical():
    narrative = "Portfolio Overview\n\nPortfolio Health Score: D\n\nDetails."
    out = ab.ensure_portfolio_health_grade_line(narrative, "B")
    assert "Portfolio Health Score: B" in out
    assert "Portfolio Health Score: D" not in out


def test_ensure_grade_line_idempotent():
    narrative = "Heading\n\nbody"
    once = ab.ensure_portfolio_health_grade_line(narrative, "A")
    twice = ab.ensure_portfolio_health_grade_line(once, "A")
    assert once == twice
    assert once.count("Portfolio Health Score: A") == 1


def test_ensure_grade_line_unknown_letter_noop():
    narrative = "Heading\n\nbody"
    assert ab.ensure_portfolio_health_grade_line(narrative, "Z") == narrative
    assert ab.ensure_portfolio_health_grade_line(narrative, "") == narrative


# --------------------------------------------------------------------------
# A2: reconcile_portfolio_prose_band widened descriptors
# --------------------------------------------------------------------------

def test_prose_band_collapses_medium_risk_state_to_low_for_grade_b():
    # Grade B implies the LOW band word.
    narrative = "The portfolio sits in a MEDIUM risk state heading into renewal."
    out = ab.reconcile_portfolio_prose_band(narrative, "B")
    assert "LOW risk state" in out
    assert "MEDIUM risk state" not in out


def test_prose_band_hyphenated_form():
    narrative = "Currently a MEDIUM-risk posture across the book."
    out = ab.reconcile_portfolio_prose_band(narrative, "B")
    assert "MEDIUM-risk posture" not in out
    assert "LOW-risk posture" in out


def test_prose_band_does_not_touch_risk_customers_counts():
    # "risk customers" is NOT a descriptor noun -> never rewritten.
    narrative = "There are 4 HIGH risk customers in the portfolio."
    out = ab.reconcile_portfolio_prose_band(narrative, "B")
    assert out == narrative


def test_prose_band_idempotent():
    narrative = "A MEDIUM risk profile remains."
    once = ab.reconcile_portfolio_prose_band(narrative, "B")
    twice = ab.reconcile_portfolio_prose_band(once, "B")
    assert once == twice


# --------------------------------------------------------------------------
# A4: _R78_STUB_RE matches the "Pattern N: Data Unavailable [Source:..]" stub
# --------------------------------------------------------------------------

def test_stub_re_matches_pattern_data_unavailable_with_citation():
    assert ab._R78_STUB_RE.match("Pattern 3: Data Unavailable [Source: AdoptIQ Report Data Sources]")
    assert ab._R78_STUB_RE.match("Pattern 1: Data unavailable.")


def test_stub_re_does_not_match_substantive_pattern_bullet():
    assert not ab._R78_STUB_RE.match("Pattern 1: Customers report slow onboarding [Source: X]")


# --------------------------------------------------------------------------
# B2: reconcile_avg_risk_claim
# --------------------------------------------------------------------------

def test_avg_risk_claim_rewrites_wrong_scale_to_canonical_10():
    narrative = "The portfolio average risk score is 26.4 across all customers."
    out = ab.reconcile_avg_risk_claim(narrative, 1.9)
    assert "average risk score is 1.9/10" in out
    assert "26.4" not in out


def test_avg_risk_claim_handles_slash_100_suffix():
    narrative = "Average risk score of 18.9/100 portfolio-wide."
    out = ab.reconcile_avg_risk_claim(narrative, 1.9)
    assert "1.9/10" in out
    assert "18.9/100" not in out


def test_avg_risk_claim_idempotent():
    narrative = "average risk score is 26.4 overall"
    once = ab.reconcile_avg_risk_claim(narrative, 1.9)
    twice = ab.reconcile_avg_risk_claim(once, 1.9)
    assert once == twice
    assert "1.9/10" in twice


def test_avg_risk_claim_noop_on_non_numeric_canonical():
    narrative = "average risk score is 26.4 overall"
    assert ab.reconcile_avg_risk_claim(narrative, None) == narrative
    assert ab.reconcile_avg_risk_claim(narrative, "n/a") == narrative


# --------------------------------------------------------------------------
# B6: reconcile_p2_active_claim
# --------------------------------------------------------------------------

def test_p2_active_leading_number_rewritten():
    narrative = "There are 5 active P2 cases requiring attention."
    out = ab.reconcile_p2_active_claim(narrative, 3)
    assert "3 active P2 cases" in out
    assert "5 active P2 cases" not in out


def test_p2_active_trailing_number_rewritten():
    narrative = "Active P2 cases: 5 across the scope."
    out = ab.reconcile_p2_active_claim(narrative, 3)
    assert "Active P2 cases: 3" in out


def test_p2_active_idempotent():
    narrative = "5 active P2 cases open"
    once = ab.reconcile_p2_active_claim(narrative, 3)
    twice = ab.reconcile_p2_active_claim(once, 3)
    assert once == twice


def test_p2_active_noop_without_p2_token():
    # A bare number with "active cases" but no P2 token must NOT be touched.
    narrative = "There are 5 active cases this quarter."
    assert ab.reconcile_p2_active_claim(narrative, 3) == narrative


def test_p2_active_noop_on_negative_canonical():
    narrative = "5 active P2 cases open"
    assert ab.reconcile_p2_active_claim(narrative, -1) == narrative
