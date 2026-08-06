"""Round 86 / Build 62 — P0/F1 regression: Compact <-> Renewal score parity.

Build 61 acceptance audit found systematic Compact-vs-Renewal score
divergence on all 239 customers in the Brian Frazier / All Contact
Center scope:

  * Compact median ``Overall_Risk_Score`` = 0.7
  * Renewal median ``Overall_Risk_Score`` = 7.1 (10x higher!)

Root cause: the R67/B1 normalization block in ``app_simple.py`` (the
``run_customer_renewal_analysis`` portfolio path) used a numeric
heuristic to guess whether the input score was on the 0-10 or 0-100
scale:

    if _r67_orig_f > 10.0:
        # treat as 0-100; project /10 to 0-10
    else:
        # treat as 0-10; multiply by 10 to publish 0-100

But ``_calculate_simple_renewal_risk`` ALWAYS returns
``renewal_risk_score`` on the 0-100 scale (passthrough of
``profile['risk_score_0_100']`` from the canonical
``compute_customer_risk_profile`` engine). For HEALTHY customers
whose 0-100 score happens to be <= 10 (the typical "no signals
observed" baseline -- portfolio-wide Webex Status incidents
contribute ~6/100, plus 0 from AB / CS / pulse / AP), the heuristic
mis-classifies the 0-100 input as a 0-10 input and publishes
``Risk_Score_0_100 = orig * 10``. Result: every HEALTHY customer's
0-100 score saturates to a phantom 71 instead of the real 7.1.

The Round 86 fix replaces the heuristic with explicit reads of
``renewal_risk_score_10`` (canonical 0-10) and ``renewal_risk_score``
(canonical 0-100) -- both are exposed by
``_calculate_simple_renewal_risk``. No scale guessing.

These tests pin the source-shape of the fix.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

import pandas as pd
import pytest

from app_simple import _calculate_simple_renewal_risk


def _portfolio_summary_loop(customer_analyses: dict, analysis_date: str = "2026-05-05",
                             next_review_date: str = "2026-06-04") -> list:
    """Replay the post-R86 portfolio summary loop logic from
    ``run_customer_renewal_analysis`` so we can pin the projection
    source-shape without driving the full Flask analysis pipeline.
    """
    rows: list[dict] = []
    for cust_name, cust_analysis in customer_analyses.items():
        # Round 86 / Build 62 (P0/F1): match the production logic.
        score_10 = cust_analysis.get('renewal_risk_score_10')
        score_100 = cust_analysis.get('renewal_risk_score')
        if score_10 is None and cust_analysis.get('overall_risk_score') is not None:
            score_10 = cust_analysis.get('overall_risk_score')
        if score_10 is None and score_100 is not None:
            try:
                score_10 = round(float(score_100) / 10.0, 2)
            except (TypeError, ValueError):
                score_10 = None
        if score_100 is None and score_10 is not None:
            try:
                score_100 = round(float(score_10) * 10.0, 1)
            except (TypeError, ValueError):
                score_100 = None
        risk_level = (
            cust_analysis.get('risk_level')
            or cust_analysis.get('renewal_risk_category')
            or 'UNKNOWN'
        )
        rows.append({
            'Customer': cust_name,
            'Overall_Risk_Score': score_10 if score_10 is not None else 0,
            'Risk_Score_0_100': score_100 if score_100 is not None else 0,
            'Risk_Level': risk_level,
            'Analysis_Date': analysis_date,
            'Next_Review_Date': next_review_date,
        })
    return rows


def test_healthy_customer_no_data_does_not_saturate_risk_score_0_100():
    """The Build 61 acceptance bug: a HEALTHY customer with no AB / CS
    / pulse / AP scored 7.1/10 in Renewal but 0.7/10 in Compact.
    Pre-R86 ``Risk_Score_0_100`` was 71 (phantom multiplication);
    post-R86 it must reflect the ACTUAL canonical 0-100 score.
    """
    # Synthesize the exact ABBOTT scenario from the Build 61 audit:
    # no AB, no CS, no pulse, no AP, no subscriptions, 36 portfolio-
    # wide Webex Status incidents (none customer-tagged so all flow
    # through to per-customer scoring per R65/R-2).
    ext_incidents = []
    for i in range(36):
        inc = {"id": f"inc-{i}", "title": f"Webex outage #{i}"}
        if i < 4:
            inc["status"] = "investigating"
        if i == 0:
            inc["impact_level"] = "high"
        ext_incidents.append(inc)

    cust = _calculate_simple_renewal_risk(
        customer_name="ABBOTT LABORATORIES US",
        customer_ab=pd.DataFrame(),
        customer_csone=pd.DataFrame(),
        team_subs_df=pd.DataFrame(),
        days=90,
        ext_incidents=ext_incidents,
    )

    # Engine returns 0-100 score under 10 for this HEALTHY customer.
    assert cust['renewal_risk_score'] is not None
    assert cust['renewal_risk_score'] < 10.0, (
        f"Test fixture assumption violated: synthesized HEALTHY customer "
        f"should score <10/100, got {cust['renewal_risk_score']}"
    )

    rows = _portfolio_summary_loop({"ABBOTT LABORATORIES US": cust})
    assert len(rows) == 1
    row = rows[0]

    # Round 86 / Build 62 (P0/F1): the saturation bug.
    # Pre-R86: Risk_Score_0_100 = orig * 10 = ~62 (HEALTHY misclassified).
    # Post-R86: Risk_Score_0_100 = engine's 0-100 directly = ~6.2.
    assert row['Risk_Score_0_100'] < 10.0, (
        f"R86/F1 regression: Risk_Score_0_100 saturated to phantom "
        f"{row['Risk_Score_0_100']} for a customer whose canonical "
        f"engine score is {cust['renewal_risk_score']}/100. The R67/B1 "
        f"numeric heuristic must NOT be used to project 0-100 from "
        f"Overall_Risk_Score; read renewal_risk_score directly."
    )

    # 0-10 must equal the engine's 0-10 score (no scale-guessing).
    assert abs(float(row['Overall_Risk_Score']) - float(cust['renewal_risk_score_10'])) < 0.01


def test_critical_customer_high_score_passes_through_unchanged():
    """Sanity guard: high-risk customers (0-100 score > 10) MUST still
    project correctly. The R86 fix preserves this branch unchanged.
    """
    # Synthesize a CRITICAL customer: many open critical AB.
    ab = pd.DataFrame([
        {"customer_name": "CRITCO", "SUBJECT_C": "Login broken",
         "SEVERITY_C": "Critical", "AB_STATUS_C": "Open"},
    ] * 8)
    cs = pd.DataFrame([
        {"customer_name": "CRITCO", "Severity": "P1", "Status": "Open"},
    ] * 4)
    cust = _calculate_simple_renewal_risk(
        customer_name="CRITCO",
        customer_ab=ab,
        customer_csone=cs,
        team_subs_df=pd.DataFrame(),
        days=90,
        ext_incidents=None,
    )
    rows = _portfolio_summary_loop({"CRITCO": cust})
    row = rows[0]
    # Both scales must agree -- this is the parity contract.
    assert abs(float(row['Risk_Score_0_100']) - float(cust['renewal_risk_score'])) < 0.5
    assert abs(float(row['Overall_Risk_Score']) - float(cust['renewal_risk_score_10'])) < 0.05


def test_explicit_score_keys_take_precedence_over_overall_risk_score():
    """Round 86 / Build 62 contract: ``renewal_risk_score`` and
    ``renewal_risk_score_10`` are the SSoT keys. The legacy
    ``overall_risk_score`` is a fallback only when the simple-analyzer
    keys are missing (single-customer RenewalAnalyzer path).
    """
    cust = {
        'renewal_risk_score': 6.2,       # 0-100 (HEALTHY baseline)
        'renewal_risk_score_10': 0.6,    # 0-10
        'overall_risk_score': 7.1,        # legacy decoy -- must be ignored
        'renewal_risk_category': 'HEALTHY',
    }
    rows = _portfolio_summary_loop({"X": cust})
    assert rows[0]['Overall_Risk_Score'] == 0.6
    assert rows[0]['Risk_Score_0_100'] == 6.2


def test_legacy_overall_risk_score_only_path_still_works():
    """Round 86 backward-compat: when ONLY ``overall_risk_score`` is
    present (single-customer analyzer path), treat it as 0-10 and
    project 0-100 = orig * 10. This preserves the legacy contract.
    """
    cust = {
        'overall_risk_score': 5.0,
        'risk_level': 'MEDIUM',
    }
    rows = _portfolio_summary_loop({"Y": cust})
    assert rows[0]['Overall_Risk_Score'] == 5.0
    assert rows[0]['Risk_Score_0_100'] == 50.0


def test_zero_score_does_not_become_negative_or_infinite():
    """Edge case: a customer with EVERY component returning None or 0
    must not produce a negative or NaN score downstream. R86 must not
    introduce new None-handling regressions.
    """
    cust = {
        'renewal_risk_score': 0.0,
        'renewal_risk_score_10': 0.0,
        'renewal_risk_category': 'HEALTHY',
    }
    rows = _portfolio_summary_loop({"Z": cust})
    assert rows[0]['Overall_Risk_Score'] == 0.0
    assert rows[0]['Risk_Score_0_100'] == 0.0


def test_pre_r86_heuristic_pattern_absent_in_app_simple():
    """Source-shape pin: the buggy R67/B1 heuristic
    (``if _r67_orig_f > 10.0`` followed by ``orig_f * 10.0``) MUST be
    replaced by the explicit 0-10 / 0-100 projection. This guards
    against accidental revert.
    """
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent / "app_simple.py"
    text = src.read_text(encoding="utf-8")
    # The post-R86 source MUST reference the explicit field names so
    # any future contributor knows the SSoT for the projection.
    assert_in_source(text, "renewal_risk_score_10", label='text')
    assert_in_source(text, "Round 86 / Build 62 (P0/F1)", label='text')
    # The original buggy pattern (multiplying Overall_Risk_Score by 10
    # to derive Risk_Score_0_100 inside the post-loop normalizer) must
    # NOT appear. The post-R86 normalizer only applies that branch
    # when the explicit 0-100 field is missing AND falls through to
    # the safer single-customer analyzer assumption.
    assert "if _r67_orig_f > 10.0:" not in text, (
        "R86/F1 regression: the buggy R67/B1 numeric heuristic is back. "
        "It mis-classifies HEALTHY customers' 0-100 scores as 0-10 "
        "inputs and saturates Risk_Score_0_100. Use renewal_risk_score "
        "/ renewal_risk_score_10 directly."
    )
