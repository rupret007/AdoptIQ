"""Round 3 regression: hardcoded date windows must be threaded
through to the underlying fetchers, and risk-band cutoffs must be
sourced from ``risk_scoring.RISK_BAND_THRESHOLDS`` rather than
hand-coded magic numbers.

These tests pin the contract surfaces only (call signatures + cutoff
constants) so they remain cheap and never reach Snowflake.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import ask_ai_grounded
from risk_scoring import RISK_BAND_THRESHOLDS


# ---------------------------------------------------------------------------
# RISK_BAND_THRESHOLDS contract
# ---------------------------------------------------------------------------


def test_risk_band_thresholds_match_canonical_75_55_35_15():
    """The canonical risk band thresholds underpin the executive
    intelligence donut, the portfolio renewal categorization, and
    every "high/medium/low" filter. Drift here would silently break
    every downstream consumer that reads from this constant.
    """
    assert RISK_BAND_THRESHOLDS["CRITICAL"] == 75
    assert RISK_BAND_THRESHOLDS["HIGH"] == 55
    assert RISK_BAND_THRESHOLDS["MEDIUM"] == 35
    assert RISK_BAND_THRESHOLDS["LOW"] == 15


# ---------------------------------------------------------------------------
# ``run_intel_grounded_ask_ai`` accepts a ``days`` parameter
# ---------------------------------------------------------------------------


def test_run_intel_grounded_ask_ai_accepts_days_param():
    """The Ask-Intel grounded path was previously hard-coded to
    365 days. Round 3 made ``days`` an explicit parameter (default
    365 to preserve prior behaviour). This test pins the signature.
    """
    sig = inspect.signature(ask_ai_grounded.run_intel_grounded_ask_ai)
    assert "days" in sig.parameters, (
        "run_intel_grounded_ask_ai must accept a `days` argument so "
        "callers (Ask-Intel UI, programmatic consumers) can scope the "
        "external intelligence window to the analysis period."
    )
    days_param = sig.parameters["days"]
    assert days_param.default == 365, (
        "Default `days` value drifted; callers depend on 365 as the "
        "back-compat default."
    )


# ---------------------------------------------------------------------------
# Source-greps (cheap CI checks that the hardcoded labels are gone)
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (_REPO_ROOT / path).read_text(encoding="utf-8")


def test_renewal_word_label_uses_dynamic_days():
    """Renewal Word dashboard must label support cases with the actual
    analysis window, not a hardcoded "Last 90 Days".
    """
    src = _read("app_simple.py")
    # The dynamic label uses an f-string; the literal "Last 90 Days"
    # string MUST NOT appear as the dashboard row label anymore.
    assert "f'Support Cases (Last {days} Days)'" in src or \
           'f"Support Cases (Last {days} Days)"' in src, (
        "Renewal Word dashboard support-cases label must be threaded "
        "with the analysis window."
    )


def test_subscription_word_recent_barriers_uses_dynamic_window():
    """The subscription Word report must derive the recent-barriers
    window from the analysis ``days`` instead of a hardcoded 30.
    """
    src = _read("app_simple.py")
    assert "barriers created in the last {_recent_window} days" in src, (
        "Subscription Word report recent-barriers sentence must use "
        "the analysis window, not a hardcoded 30."
    )


def test_csconsole_briefing_recent_barriers_explicit_label():
    """The CSConsole briefing must explicitly label the 30-day window
    as a "short-horizon spotlight" so readers do not assume the
    headline number reflects the full analysis window.
    """
    src = _read("adoptiq_backend.py")
    assert "short-horizon spotlight" in src, (
        "CSConsole briefing must annotate the 30-day Recent Barriers "
        "block as a short-horizon spotlight."
    )


def test_service_incidents_chart_title_is_status_based():
    """The service-incidents bar chart title was previously labelled
    "Red = High Impact" while the colouring code keyed off the
    incident status. The title must now match the actual logic.
    """
    src = _read("app_simple.py")
    assert "active investigation" in src, (
        "Service Incidents Timeline title must describe the status-"
        "based colouring (e.g. 'Red = active investigation')."
    )
