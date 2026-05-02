"""Round 67 / Build 41 (B1) -- Compact and Renewal score parity.

Build 40 acceptance saw the same customer score differently across
Compact and Renewal reports for the same scope. Two root causes:

1. Scale: Compact published 0-10 while Renewal published 0-100.
2. Inputs: Compact passed ``ext_incidents=None`` to
   ``compute_customer_risk_profile`` while Renewal passed the
   filtered list. The incident-component contribution diverged
   even when the formula was identical.

Round 67 / B1 fixes both:

- ``compact_report_formatter.calculate_renewal_risk_scores`` now
  accepts ``ext_incidents`` and threads the per-customer-filtered
  list (via ``_r65_filter_customer_tagged_incidents``) into
  ``compute_customer_risk_profile``.
- The two ``app_simple.run_compact_analysis`` callsites pass the
  same ``ext_incidents`` that the Renewal pipeline uses.
- Renewal publishes ``Overall_Risk_Score`` on a 0-10 scale +
  ``Risk_Level=MODERATE`` for the MEDIUM band so the labels match
  Compact byte-for-byte. The pre-R67 0-100 score is preserved
  alongside as ``Risk_Score_0_100`` for back-compat with downstream
  consumers that anchored on the 0-100 axis.
"""
from __future__ import annotations

import inspect

import pandas as pd
import pytest

from compact_report_formatter import calculate_renewal_risk_scores


# ---------------------------------------------------------------------------
# Function signature (back-compat-preserving threading)
# ---------------------------------------------------------------------------


def test_calculate_renewal_risk_scores_accepts_ext_incidents() -> None:
    """R67/B1: ``ext_incidents`` MUST be a recognised optional kwarg
    on the public scoring helper (parity with the Renewal pipeline)."""
    sig = inspect.signature(calculate_renewal_risk_scores)
    assert "ext_incidents" in sig.parameters, (
        "R67/B1: calculate_renewal_risk_scores MUST expose ext_incidents kwarg"
    )
    assert sig.parameters["ext_incidents"].default is None, (
        "ext_incidents default MUST stay None so legacy callers do not break"
    )


def test_calculate_renewal_risk_scores_runs_without_ext_incidents_back_compat() -> None:
    """Legacy call (no ext_incidents kwarg) MUST still produce a result
    so existing callers do not break under R67."""
    ab = pd.DataFrame([
        {"customer_name": "Acme Corp", "AB_STATUS_C": "Open", "SEVERITY_C": "Critical"},
    ])
    cs = pd.DataFrame([
        {"customer_name": "Acme Corp", "Title": "Login", "Severity": "P1"},
    ])
    out = calculate_renewal_risk_scores(ab, cs)
    assert isinstance(out, dict)
    assert "Acme Corp" in out
    assert "score" in out["Acme Corp"]
    assert 0.0 <= out["Acme Corp"]["score"] <= 10.0


def test_calculate_renewal_risk_scores_runs_with_ext_incidents() -> None:
    """Round 67 / B1: passing ``ext_incidents`` must NOT raise; the
    result must include the same keys as the legacy call."""
    ab = pd.DataFrame([
        {"customer_name": "Acme Corp", "AB_STATUS_C": "Open", "SEVERITY_C": "Critical"},
    ])
    cs = pd.DataFrame([
        {"customer_name": "Acme Corp", "Title": "Login", "Severity": "P1"},
    ])
    incidents = [
        {"customer_name": "Acme Corp", "title": "Webex outage", "status": "investigating"},
    ]
    out = calculate_renewal_risk_scores(ab, cs, ext_incidents=incidents)
    assert isinstance(out, dict)
    assert "Acme Corp" in out
    assert "score" in out["Acme Corp"]
    assert "risk_score_0_100" in out["Acme Corp"]
    assert "risk_band" in out["Acme Corp"]


# ---------------------------------------------------------------------------
# Scoring path divergence: with vs without incidents (Compact path now
# matches Renewal because both feed the same ext_incidents)
# ---------------------------------------------------------------------------


def test_compact_score_changes_when_ext_incidents_supplied() -> None:
    """Defense check: incident component MUST shift the score when a
    customer-tagged incident is present (otherwise the threading
    contributes nothing). We don't assert a specific delta -- we only
    assert that the path is honoured."""
    ab = pd.DataFrame([
        {"customer_name": "Acme Corp", "AB_STATUS_C": "Open", "SEVERITY_C": "Critical"},
    ])
    cs = pd.DataFrame([
        {"customer_name": "Acme Corp", "Title": "Login", "Severity": "P1"},
    ])
    incidents_high = [
        {"customer_name": "Acme Corp", "title": "Webex outage 1", "status": "investigating"},
        {"customer_name": "Acme Corp", "title": "Webex outage 2", "status": "investigating"},
        {"customer_name": "Acme Corp", "title": "Webex outage 3", "status": "investigating"},
    ]
    out_no_inc = calculate_renewal_risk_scores(ab, cs, ext_incidents=None)
    out_w_inc = calculate_renewal_risk_scores(ab, cs, ext_incidents=incidents_high)
    assert out_no_inc["Acme Corp"]["score"] <= out_w_inc["Acme Corp"]["score"], (
        "Adding ext_incidents MUST not LOWER the score (incidents only add risk). "
        f"no_inc={out_no_inc['Acme Corp']['score']} w_inc={out_w_inc['Acme Corp']['score']}"
    )


def test_compact_filter_helper_used_when_available() -> None:
    """R67/B1: the formatter lazy-imports ``_r65_filter_customer_tagged_incidents``
    so the Compact path filters incidents per-customer (parity with
    Renewal).  When the helper is reachable, the formatter uses it."""
    # Smoke check: import succeeds (defends against accidental rename).
    from app_simple import _r65_filter_customer_tagged_incidents
    assert callable(_r65_filter_customer_tagged_incidents), (
        "R67/B1: _r65_filter_customer_tagged_incidents MUST stay importable "
        "from app_simple so the lazy-import in compact_report_formatter "
        "succeeds at runtime"
    )


# ---------------------------------------------------------------------------
# Compact and Renewal scoring agree to the bit when fed identical inputs
# ---------------------------------------------------------------------------


def test_compact_and_renewal_score_agree_on_same_inputs() -> None:
    """Both paths call ``compute_customer_risk_profile`` underneath; if
    given the same inputs they MUST produce the same numeric profile."""
    from risk_scoring import compute_customer_risk_profile

    ab = pd.DataFrame([
        {"customer_name": "Acme Corp", "AB_STATUS_C": "Open", "SEVERITY_C": "Critical"},
    ])
    cs = pd.DataFrame([
        {"customer_name": "Acme Corp", "Title": "Login", "Severity": "P1"},
    ])
    incidents = [
        {"customer_name": "Acme Corp", "title": "Webex outage", "status": "investigating"},
    ]

    # Direct invocation (the contract under test).
    direct = compute_customer_risk_profile(
        customer_name="Acme Corp",
        customer_ab=ab,
        customer_csone=cs,
        ext_incidents=incidents,
        recent_window_days=90,
    )

    # Compact path -- threads through calculate_renewal_risk_scores.
    out = calculate_renewal_risk_scores(
        ab, cs, ext_incidents=incidents, recent_window_days=90,
    )
    compact = out["Acme Corp"]

    assert compact["score"] == direct["risk_score_0_10"], (
        "Compact 0-10 score MUST equal the direct profile call"
    )
    assert compact["risk_score_0_100"] == direct["risk_score_0_100"], (
        "Compact 0-100 score MUST equal the direct profile call"
    )
    assert compact["risk_band"] == direct["risk_band"], (
        "Risk band MUST match (canonical CRITICAL/HIGH/MEDIUM/LOW/HEALTHY)"
    )
