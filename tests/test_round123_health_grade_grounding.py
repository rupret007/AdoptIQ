"""Round 123 / Build 92 -- Customer Health Grade canonical-grounding (hybrid).

Pins the fix for the only genuine accuracy defect found in the Build-91
deep dive: the Comprehensive per-customer ``Customer Health Score:
<letter>`` (and the ``Portfolio Health Score``) were LLM-discretion and
routinely contradicted the canonical ``Risk_Components`` band on the SAME
report (a HEALTHY customer graded ``F``; a HIGH customer graded ``C``).

The hybrid fix has three layers, each pinned here:
  1. ``risk_scoring.band_to_health_grade`` -- canonical band -> letter SSoT.
  2. the briefing/prompt grounding (source-shape pins).
  3. ``adoptiq_backend.stamp_customer_health_grade`` -- the deterministic
     post-generation guarantee + the drift diagnostic.
"""

from source_shape_utils import assert_in_source
import re
from pathlib import Path

import pytest

import risk_scoring as rs
import adoptiq_backend as ab


# ---------------------------------------------------------------------------
# 1. Canonical band -> letter SSoT
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "band,expected",
    [
        ("HEALTHY", "A"),
        ("LOW", "B"),
        ("MEDIUM", "C"),
        ("MODERATE", "C"),  # display-vocabulary alias for the MEDIUM band
        ("HIGH", "D"),
        ("CRITICAL", "F"),
        # case / whitespace tolerance
        ("healthy", "A"),
        ("  High ", "D"),
        ("CrItIcAl", "F"),
    ],
)
def test_band_to_health_grade_exhaustive_mapping(band, expected):
    assert rs.band_to_health_grade(band) == expected


def test_band_to_health_grade_score_fallback_when_band_missing():
    # When the band is missing / unrecognised, derive from the 0-100 score
    # via the canonical RISK_BAND_THRESHOLDS (CRITICAL>=75 ... HEALTHY<15).
    assert rs.band_to_health_grade(None, 5.6) == "A"     # HEALTHY band
    assert rs.band_to_health_grade(None, 20.0) == "B"    # LOW band
    assert rs.band_to_health_grade(None, 40.0) == "C"    # MEDIUM band
    assert rs.band_to_health_grade(None, 62.0) == "D"    # HIGH band
    assert rs.band_to_health_grade(None, 90.0) == "F"    # CRITICAL band
    # unrecognised band string still falls through to the score
    assert rs.band_to_health_grade("bogus", 90.0) == "F"


def test_band_to_health_grade_defaults_to_a_with_no_signal():
    assert rs.band_to_health_grade(None, None) == "A"
    assert rs.band_to_health_grade("", None) == "A"


def test_health_grade_for_profile_reads_band_then_score():
    # The Build-91 contradictions, expressed as profiles:
    assert rs.health_grade_for_profile(
        {"risk_band": "HEALTHY", "risk_score_0_100": 5.6}
    ) == "A"  # ZONES INC -- was graded F
    assert rs.health_grade_for_profile(
        {"risk_band": "HIGH", "risk_score_0_100": 62.0}
    ) == "D"  # WINTRUST -- was graded C
    # band missing -> score fallback
    assert rs.health_grade_for_profile({"risk_score_0_100": 80.0}) == "F"
    # non-dict / empty -> safe default
    assert rs.health_grade_for_profile(None) == "A"
    assert rs.health_grade_for_profile("nope") == "A"


def test_portfolio_health_grade_from_average_score():
    assert rs.portfolio_health_grade({"average_risk_score_0_100": 12.0}) == "A"
    assert rs.portfolio_health_grade({"average_risk_score_0_100": 60.0}) == "D"
    assert rs.portfolio_health_grade({}) == "A"
    assert rs.portfolio_health_grade(None) == "A"


def test_band_thresholds_mapping_is_consistent_with_risk_band():
    # Every band the canonical _risk_band can emit must map to a letter.
    for score in (0, 14, 15, 34, 35, 54, 55, 74, 75, 100):
        band = rs._risk_band(float(score))
        assert rs.band_to_health_grade(band) in {"A", "B", "C", "D", "F"}
        # the band-direct path and the score-fallback path must agree
        assert rs.band_to_health_grade(band) == rs.band_to_health_grade(None, score)


# ---------------------------------------------------------------------------
# 2. Deterministic stamp + extract (the guarantee)
# ---------------------------------------------------------------------------

def test_extract_customer_health_grade_shapes():
    assert ab.extract_customer_health_grade("Customer Health Score: F") == "F"
    assert ab.extract_customer_health_grade("Customer Health Score: [c]") == "C"
    assert ab.extract_customer_health_grade("### **Customer Health Score: B**") == "B"
    assert ab.extract_customer_health_grade("no grade here") is None
    assert ab.extract_customer_health_grade(None) is None


def test_stamp_customer_health_grade_rewrites_value():
    # plain letter
    assert ab.stamp_customer_health_grade("Customer Health Score: F", "A") \
        == "Customer Health Score: A"
    # bracketed + lowercase -> brackets dropped, canon written
    assert ab.stamp_customer_health_grade("Customer Health Score: [c]", "D") \
        == "Customer Health Score: D"
    # bold/heading chrome preserved, only the letter changes
    out = ab.stamp_customer_health_grade("### **Customer Health Score: F**", "A")
    assert out == "### **Customer Health Score: A**"
    # narrative after the letter is preserved
    out2 = ab.stamp_customer_health_grade(
        "### **Customer Health Score: F**\nZONES is in crisis.", "A"
    )
    assert "Customer Health Score: A" in out2
    assert "ZONES is in crisis." in out2


def test_stamp_customer_health_grade_is_idempotent():
    txt = "### **Customer Health Score: F**\nbody"
    once = ab.stamp_customer_health_grade(txt, "A")
    twice = ab.stamp_customer_health_grade(once, "A")
    assert once == twice


def test_stamp_never_writes_out_of_range_letter():
    txt = "Customer Health Score: F"
    # E / G / junk are not valid grades -> leave the text untouched
    assert ab.stamp_customer_health_grade(txt, "E") == txt
    assert ab.stamp_customer_health_grade(txt, "") == txt
    assert ab.stamp_customer_health_grade(txt, None) == txt


def test_stamp_does_not_corrupt_words_or_other_brackets():
    # Round 139: band-word tokens on the grade line are intentionally replaced.
    w = "Customer Health Score: Critical situation"
    assert ab.stamp_customer_health_grade(w, "A") == "Customer Health Score: A situation"
    # Band words elsewhere in the narrative (not on the grade line) stay untouched.
    prose = "The customer faces a Critical operational situation."
    assert ab.stamp_customer_health_grade(prose, "A") == prose
    # Non-grade brackets elsewhere are untouched; only the grade changes.
    nb = "See theme [Onboarding] and Customer Health Score: F"
    out = ab.stamp_customer_health_grade(nb, "A")
    assert "[Onboarding]" in out
    assert out.endswith("Customer Health Score: A")


def test_portfolio_stamp_and_extract():
    p = "## **Portfolio Health Score: F**"
    assert ab.extract_portfolio_health_grade(p) == "F"
    assert ab.stamp_portfolio_health_grade(p, "A") == "## **Portfolio Health Score: A**"
    # idempotent + no-op on missing line
    assert ab.stamp_portfolio_health_grade("no grade", "A") == "no grade"


def test_stamp_no_op_when_grade_already_canonical():
    txt = "Customer Health Score: B"
    assert ab.stamp_customer_health_grade(txt, "B") == txt


# ---------------------------------------------------------------------------
# 3. Drift diagnostic rollup (PII-safe)
# ---------------------------------------------------------------------------

def test_record_health_grade_outcome_drift_rollup():
    import app_simple as app

    status = {}
    # a drift (LLM said F, canon A) + a non-drift (LLM B, canon B)
    app._r123_record_health_grade_outcome(
        status, customer_name="ZONES INC US", llm_letter="F",
        canonical_letter="A", scope="customer",
    )
    app._r123_record_health_grade_outcome(
        status, customer_name="HEALTHY CO", llm_letter="B",
        canonical_letter="B", scope="customer",
    )
    diag = status["health_grade_diag"]
    assert diag["total"] == 2
    assert diag["stamped"] == 2       # both wrote a valid canonical letter
    assert diag["drifted"] == 1       # only ZONES drifted
    by = diag["by_customer"]
    # only the drift is recorded in by_customer
    assert len(by) == 1
    # PII-safe: the raw customer name must NOT appear as a key
    assert "ZONES INC US" not in by
    digest = app._id_digest("ZONES INC US")
    assert digest in by
    rec = by[digest]
    assert rec["llm"] == "F"
    assert rec["canonical"] == "A"
    assert rec["scope"] == "customer"
    assert "recorded_at" in rec


def test_record_health_grade_outcome_portfolio_key():
    import app_simple as app

    status = {}
    app._r123_record_health_grade_outcome(
        status, customer_name=None, llm_letter="F",
        canonical_letter="A", scope="portfolio",
    )
    diag = status["health_grade_diag"]
    assert diag["drifted"] == 1
    assert "portfolio" in diag["by_customer"]


def test_record_health_grade_outcome_no_llm_letter_counts_but_no_drift():
    import app_simple as app

    status = {}
    # extract returned None (LLM omitted the line) -> stamped, not drift
    app._r123_record_health_grade_outcome(
        status, customer_name="X CO", llm_letter=None,
        canonical_letter="C", scope="customer",
    )
    diag = status["health_grade_diag"]
    assert diag["total"] == 1
    assert diag["stamped"] == 1
    assert diag.get("drifted", 0) == 0
    assert diag.get("by_customer", {}) == {}


# ---------------------------------------------------------------------------
# 4. End-to-end: canonical band -> stamped narrative letter agrees
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "band,score,expected_letter",
    [
        ("HEALTHY", 5.6, "A"),   # ZONES INC -- was F
        ("HIGH", 62.0, "D"),     # WINTRUST -- was C
        ("CRITICAL", 90.0, "F"),
        ("LOW", 20.0, "B"),
        ("MEDIUM", 40.0, "C"),
    ],
)
def test_end_to_end_band_to_stamped_letter(band, score, expected_letter):
    profile = {"risk_band": band, "risk_score_0_100": score}
    canon = rs.health_grade_for_profile(profile)
    assert canon == expected_letter
    # the LLM wrote a contradicting letter; the stamp corrects it.
    narrative = "### **Customer Health Score: F**\nSome justification text."
    stamped = ab.stamp_customer_health_grade(narrative, canon)
    assert ab.extract_customer_health_grade(stamped) == expected_letter


# ---------------------------------------------------------------------------
# 5. Source-shape pins (briefing threading + prompt grounding)
# ---------------------------------------------------------------------------

_APP_SRC = Path(__file__).resolve().parent.parent / "app_simple.py"
_BACKEND_SRC = Path(__file__).resolve().parent.parent / "adoptiq_backend.py"


def test_per_customer_briefing_threads_risk_profiles():
    src = _APP_SRC.read_text(encoding="utf-8")
    # The per-customer _create_briefing_book call must pass the
    # single-entry risk_profiles so the briefing emits Canonical Risk Bands.
    assert_in_source(src, "_r123_cust_risk_profiles", label='src')
    assert_in_source(src, "risk_profiles=_r123_cust_risk_profiles", label='src')


def test_per_customer_stamp_wired_into_loop():
    src = _APP_SRC.read_text(encoding="utf-8")
    assert_in_source(src, "stamp_customer_health_grade(", label='src')
    assert_in_source(src, "_r123_health_grade_for_profile(_r123_cust_profile)", label='src')
    assert_in_source(src, "_r123_record_health_grade_outcome(", label='src')


def test_portfolio_stamp_wired_into_gate():
    src = _APP_SRC.read_text(encoding="utf-8")
    assert_in_source(src, "stamp_portfolio_health_grade(", label='src')
    assert_in_source(src, "_r123_portfolio_health_grade(portfolio_risk_summary)", label='src')


def test_prompts_pin_grade_to_canonical_band():
    src = _BACKEND_SRC.read_text(encoding="utf-8")
    # Customer prompt names the exact mapping and forbids qualitative guessing.
    assert_in_source(src, "MUST equal the canonical risk band", label='src')
    assert_in_source(src, "A = HEALTHY, B = LOW, C = MEDIUM", label='src')
    # Portfolio prompt pins to the canonical band distribution.
    assert_in_source(src, "MUST agree with the canonical portfolio risk-band distribution", label='src')


def test_briefing_block_reads_both_key_shapes():
    src = _BACKEND_SRC.read_text(encoding="utf-8")
    # The Canonical Risk Bands block must read the canonical profile keys
    # (risk_score_0_100 / risk_band), not only the legacy risk_score / risk_level.
    assert_in_source(src, "risk_score_0_100", label='src')
    assert_in_source(src, 'get("risk_level") or _r25c_profile.get("risk_band")', label='src')


def test_round123_source_markers_present():
    app_src = _APP_SRC.read_text(encoding="utf-8")
    backend_src = _BACKEND_SRC.read_text(encoding="utf-8")
    rs_src = (Path(__file__).resolve().parent.parent / "risk_scoring.py").read_text(encoding="utf-8")
    assert_in_source(app_src, "Round 123", label='app_src')
    assert "Round 123" in backend_src
    assert "Round 123" in rs_src
