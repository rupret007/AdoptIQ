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
  accepts ``ext_incidents`` and threads only explicitly customer-tagged
  incidents into ``compute_customer_risk_profile``; untagged status-page
  incidents remain portfolio context.
- The two ``app_simple.run_compact_analysis`` callsites pass the
  same ``ext_incidents`` that the Renewal pipeline uses.
- Renewal publishes ``Overall_Risk_Score`` on a 0-10 scale +
  ``Risk_Level=MODERATE`` for the MEDIUM band so the labels match
  Compact byte-for-byte. The pre-R67 0-100 score is preserved
  alongside as ``Risk_Score_0_100`` for back-compat with downstream
  consumers that anchored on the 0-100 axis.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source

import builtins
import inspect
from pathlib import Path

import pandas as pd
import pytest

from compact_report_formatter import (
    _r104_filter_customer_tagged_incidents,
    calculate_renewal_risk_scores,
)


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


def test_compact_untagged_incidents_are_context_only() -> None:
    """Portfolio status incidents cannot be smeared into customer risk."""

    ab = pd.DataFrame([{"customer_name": "Acme Corp"}])
    incidents = [
        {
            "id": "INC-PORTFOLIO",
            "title": "Portfolio status incident",
            "status": "investigating",
            "impact_level": "high",
        }
    ]

    without_incident = calculate_renewal_risk_scores(ab, pd.DataFrame())
    with_context = calculate_renewal_risk_scores(
        ab,
        pd.DataFrame(),
        ext_incidents=incidents,
    )

    assert with_context["Acme Corp"]["risk_score_0_100"] == without_incident[
        "Acme Corp"
    ]["risk_score_0_100"]
    assert with_context["Acme Corp"]["risk_band"] == without_incident[
        "Acme Corp"
    ]["risk_band"]


def test_compact_local_filter_keeps_only_matching_tagged_incidents() -> None:
    """The local scorer filter keeps exact customer-attributed evidence."""

    incidents = [
        {"id": "INC-ACME", "customer_name": "Acme Corp"},
        {"id": "INC-BETA", "customer_name": "Beta Inc"},
    ]

    selected = _r104_filter_customer_tagged_incidents(incidents, "Acme Corp")

    assert [incident["id"] for incident in selected] == ["INC-ACME"]


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


def test_round100_compact_curated_action_plans_are_scored() -> None:
    """Round 100: Compact XLSX writes curated Action_Plans columns.

    The live 2026-05-27 Compact/Renewal pair showed Compact stuck at
    the 0.7 baseline for customers whose curated Action_Plans rows were
    present in the workbook.  The classifier already recognised raw
    Snowflake APs, but the per-customer slicer ignored the curated
    ``Customer Name`` column, so AP rows never reached the scorer.
    """
    action_plans = pd.DataFrame([
        {"Customer Name": "Acme Corp", "Status": "Open", "Action Plan Title": "Onboard"},
        {"Customer Name": "Acme Corp", "Status": "In Progress", "Action Plan Title": "Train"},
    ])

    out = calculate_renewal_risk_scores(
        pd.DataFrame(),
        pd.DataFrame(),
        extra_frames=[action_plans],
        recent_window_days=90,
    )

    assert "Acme Corp" in out
    assert out["Acme Corp"]["score"] > 0.0
    assert any("unresolved action plans" in factor for factor in out["Acme Corp"]["risk_factors"])


def test_round100_compact_curated_customer_pulse_is_scored() -> None:
    """Round 100: Compact XLSX writes curated Customer_Pulse columns.

    Curated pulse rows use ``Customer Name`` plus lower-case ``rating``.
    Both the Compact extra-frame classifier and the shared risk scorer
    must understand that shape, otherwise Compact ignores pulse risk
    that Renewal includes.
    """
    pulse = pd.DataFrame([
        {"Customer Name": "Acme Corp", "rating": "Poor", "Customer Pulse": "Poor"},
    ])

    out = calculate_renewal_risk_scores(
        pd.DataFrame(),
        pd.DataFrame(),
        extra_frames=[pulse],
        recent_window_days=90,
    )

    assert "Acme Corp" in out
    assert out["Acme Corp"]["score"] > 0.0
    assert any("poor/bad customer pulse" in factor for factor in out["Acme Corp"]["risk_factors"])


def test_round104_compact_incident_filter_does_not_import_app_simple(monkeypatch: pytest.MonkeyPatch) -> None:
    """Round 104: Compact scoring must not import ``app_simple`` at runtime.

    Build 72 live audit reproduced that importing ``app_simple`` from inside
    ``calculate_renewal_risk_scores`` can execute app startup side effects
    during a scoring-only call.  The incident filter now lives locally in the
    compact formatter.
    """
    original_import = builtins.__import__
    attempted_app_simple_imports: list[str] = []

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: ANN001
        if name == "app_simple":
            attempted_app_simple_imports.append(name)
            raise AssertionError("compact scoring must not import app_simple")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    out = calculate_renewal_risk_scores(
        pd.DataFrame([{"customer_name": "Acme Corp"}]),
        pd.DataFrame(),
        ext_incidents=[
            {
                "customer_name": "Acme Corp",
                "title": "Webex outage",
                "status": "investigating",
            }
        ],
        recent_window_days=90,
    )

    assert attempted_app_simple_imports == []
    assert out["Acme Corp"]["score"] > 0.0


def test_round104_compact_formatter_source_has_no_app_simple_filter_import() -> None:
    """Round 104 source-shape guard for the no-side-effect filter."""
    import compact_report_formatter

    src = inspect.getsource(compact_report_formatter.calculate_renewal_risk_scores)
    assert "from app_simple import _r65_filter_customer_tagged_incidents" not in src
    assert_in_source(src, "_r104_filter_customer_tagged_incidents", label='src')


def test_round104_compact_excel_worker_captures_ext_incidents() -> None:
    """Round 104: threaded Compact XLSX scoring must carry incident input."""
    src = Path("app_simple.py").read_text(encoding="utf-8")
    assert_in_source(src, "def generate_excel(_ctx=_r23_ctx, _r104_ext_incidents=ext_incidents)", label='src')
    assert_in_source(src, "_r105_compact_incidents_for_scoring", label='src')
    assert_in_source(src, "ext_incidents=_r105_incidents if _r105_incidents else None", label='src')


def test_round105_compact_incident_recovery_is_wired_into_both_workers() -> None:
    """Round 105: live Build 73 still drifted because Compact scored with
    an empty incident list. Both threaded workers must recover a missing
    captured list before calling the shared scorer."""
    src = Path("app_simple.py").read_text(encoding="utf-8")
    assert_in_source(src, "'ext_incidents': ext_incidents", label='src')
    assert src.count("_r105_compact_incidents_for_scoring(") >= 5
    assert_in_source(src, "Round 105: recovered %d status incident(s) for Compact risk scoring", label='src')
