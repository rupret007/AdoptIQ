"""Round 65 / C-1 regression: Portfolio Overview fallback now
distinguishes R25B/R25C numeric or risk-band drift from generic
builder errors.

Build 37 reference run for Brian Frazier / All Contact Center / 90d
emitted the bland fallback paragraph::

    "Portfolio-level AI summary unavailable for this run (builder
    error after 1 LLM attempts; last_error_kind=ValueError)."

That paragraph was technically correct -- the validator did raise
``ValueError`` -- but it was misleading because it looked like a
code regression when the actual cause was the LLM rendering a
number that disagreed with the canonical pipeline.  The Round 65
fix has two pieces and both are pinned by this test:

1. ``report_consistency.validate_word_numeric_drift`` (and the
   risk-band sibling) attach a structured ``drift_detail`` dict
   to the raised ``ValueError`` AND surface it in the returned
   ``ConsistencyResultContract`` for non-raising callers.

2. ``app_simple.py`` reads ``drift_detail`` off the exception and
   renders an operator-actionable fallback paragraph naming the
   drifted field, the LLM's claim, and the canonical truth.

This test pins both halves at the source-shape level (the
validators) AND at the integration level (a synthetic narrative
fed end-to-end through ``validate_word_numeric_drift`` with
``raise_on_drift=True``, asserting the exception carries the
structured detail an outer ``except ValueError`` branch can render).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from report_consistency import (
    validate_word_numeric_drift,
    validate_word_risk_band_claims,
)


_CANONICAL_TOTALS = {
    "total_customers": 37,
    "total_barriers": 67,
    "total_cases": 412,
    "bems_count": 5,
}


def _drifted_portfolio_snapshot() -> str:
    """Narrative reproducing the Build 37 reference drift."""
    return (
        "## Executive Summary: What's Really Happening\n"
        "\n"
        "Portfolio Snapshot:\n"
        "- Total Customers: 27\n"
        "- Active Adoption Barriers: 67\n"
        "- TAC Cases: 412\n"
        "- BEMS Escalations: 5\n"
    )


# ---------------------------------------------------------------------------
# Validator-level pins (drift_detail attached to ValueError + result dict)
# ---------------------------------------------------------------------------


def test_numeric_drift_value_error_carries_structured_detail() -> None:
    """``validate_word_numeric_drift(raise_on_drift=True)`` attaches a
    structured ``drift_detail`` dict to the raised ``ValueError`` so
    the caller's ``except ValueError`` branch can render an
    operator-actionable fallback paragraph without re-running the
    validator."""

    with pytest.raises(ValueError) as exc_info:
        validate_word_numeric_drift(
            _drifted_portfolio_snapshot(),
            _CANONICAL_TOTALS,
            raise_on_drift=True,
        )

    detail = getattr(exc_info.value, "drift_detail", None)
    assert isinstance(detail, dict), (
        "Round 65 / C-1: ValueError raised by validate_word_numeric_drift "
        "must carry a structured ``drift_detail`` attribute so the outer "
        "except branch can render a specific fallback paragraph."
    )
    assert detail.get("kind") == "numeric"
    assert detail.get("validator") == "validate_word_numeric_drift"
    assert detail.get("round") == "R25B"

    fields = detail.get("fields") or []
    assert isinstance(fields, list) and fields, (
        "drift_detail['fields'] must enumerate the drifted metrics so the "
        "operator can act without re-running the validator."
    )

    customers_field = next(
        (f for f in fields if f.get("field") == "total_customers"), None
    )
    assert customers_field is not None, (
        "Build 37 reference drift was Total Customers (27 vs canonical 37) -- "
        "drift_detail must enumerate it explicitly."
    )
    assert customers_field.get("canonical_value") == 37
    assert 27 in (customers_field.get("llm_values") or [])
    assert 27 in (customers_field.get("drifted_values") or [])
    assert customers_field.get("label"), "drifted field must carry a human label"


def test_numeric_drift_returns_drift_detail_in_result_dict() -> None:
    """Non-raising callers (e.g. ``ADOPTIQ_NONSTRICT_R25B=1`` mode)
    must also see the structured ``drift_detail`` in the result dict
    so the admin dashboard can surface it without catching exceptions.
    """
    result = validate_word_numeric_drift(
        _drifted_portfolio_snapshot(),
        _CANONICAL_TOTALS,
        raise_on_drift=False,
    )
    assert result["is_valid"] is False
    detail = result.get("drift_detail")
    assert isinstance(detail, dict)
    assert detail.get("kind") == "numeric"
    assert any(
        f.get("field") == "total_customers" for f in (detail.get("fields") or [])
    )


def test_numeric_drift_clean_narrative_emits_empty_fields_list() -> None:
    """A clean narrative must still expose the ``drift_detail`` shape
    (with an empty ``fields`` list) so downstream code can rely on the
    key always being present."""
    clean = (
        "Portfolio Snapshot:\n"
        "- Total Customers: 37\n"
        "- Active Adoption Barriers: 67\n"
        "- TAC Cases: 412\n"
        "- BEMS Escalations: 5\n"
    )
    result = validate_word_numeric_drift(
        clean, _CANONICAL_TOTALS, raise_on_drift=False
    )
    assert result["is_valid"] is True
    assert isinstance(result.get("drift_detail"), dict)
    assert result["drift_detail"].get("fields") == []


def test_risk_band_drift_value_error_carries_structured_detail() -> None:
    """The risk-band validator mirrors the numeric one: drift raises
    ``ValueError`` with ``drift_detail`` attached, naming
    ``high_risk_customers`` (CRITICAL+HIGH narrative count vs
    canonical) or ``invalid_band_labels`` (compound/synonym labels)."""

    drifted = (
        "Risk Level: HIGH/CRITICAL\n"
        "Risk Level: MODERATE\n"
        "Risk Level: HIGH\n"
    )
    with pytest.raises(ValueError) as exc_info:
        validate_word_risk_band_claims(
            drifted,
            canonical_high_risk_customers=4,
            raise_on_drift=True,
        )

    detail = getattr(exc_info.value, "drift_detail", None)
    assert isinstance(detail, dict)
    assert detail.get("kind") == "risk_band"
    assert detail.get("validator") == "validate_word_risk_band_claims"
    assert detail.get("round") == "R25C"

    fields = detail.get("fields") or []
    assert fields, "risk-band drift_detail['fields'] must not be empty"
    field_names = {f.get("field") for f in fields if isinstance(f, dict)}
    assert (
        "invalid_band_labels" in field_names
        or "high_risk_customers" in field_names
    ), (
        f"risk-band drift_detail must enumerate either invalid_band_labels "
        f"or high_risk_customers; got fields={field_names!r}"
    )


# ---------------------------------------------------------------------------
# app_simple.py source-shape pin: the fallback branch reads drift_detail
# off the exception and emits a specific paragraph.
# ---------------------------------------------------------------------------


def test_app_simple_portfolio_fallback_reads_drift_detail() -> None:
    """app_simple.py's portfolio-error ``except`` block now extracts
    ``drift_detail`` from the raised ValueError and renders a
    "numeric drift detected" or "risk-band drift detected" paragraph
    instead of the generic "builder error" boilerplate.

    Pre-Round-65 the entire ``except Exception as portfolio_error``
    branch only emitted the generic "builder error after N LLM
    attempts; last_error_kind=ValueError" string.  This test pins
    that the new branch is present in source.
    """
    src = Path("app_simple.py").read_text(encoding="utf-8")

    # The new branch reads the structured detail off the exception.
    assert 'getattr(portfolio_error, "drift_detail", None)' in src, (
        "Round 65 / C-1: app_simple.py portfolio-error branch must read "
        "``drift_detail`` off the raised ValueError so it can render a "
        "specific fallback instead of the generic builder-error paragraph."
    )

    # The new branch surfaces the structured detail in the diag dict
    # so the admin dashboard can display it.
    assert '_r64_portfolio_diag["drift_detail"]' in src, (
        "Round 65 / C-1: drift_detail must be surfaced in "
        "_r64_portfolio_diag for the admin dashboard."
    )

    # The new branch emits a "numeric drift detected" / "risk-band
    # drift detected" paragraph -- specific, not generic.
    assert "numeric drift detected" in src, (
        "Round 65 / C-1: portfolio-error fallback must say 'numeric drift "
        "detected' (specific) instead of 'builder error' (generic) when "
        "the validator caught a numeric drift."
    )

    # The new branch enumerates the drifted fields with canonical
    # truth + LLM claim so the operator can act on it.
    assert "Drifted fields:" in src, (
        "Round 65 / C-1: portfolio-error fallback must list the drifted "
        "fields with their canonical and LLM-narrated values so the "
        "operator can act without re-running the validator."
    )


def test_app_simple_portfolio_fallback_preserves_legacy_branch() -> None:
    """The new drift-detail branch is gated by an ``isinstance`` check
    so genuine builder errors (e.g. NetworkError, AttributeError, a
    ValueError that does NOT carry drift_detail) still flow through
    the legacy "builder error" fallback paragraph.  Pin both branches
    coexist."""
    src = Path("app_simple.py").read_text(encoding="utf-8")

    # New branch is conditional on drift_detail presence.
    assert re.search(
        r"if isinstance\(_r65_drift_detail, dict\) and _r65_drift_detail\.get\([\"']fields[\"']\)",
        src,
    ), (
        "Round 65 / C-1: drift-detail branch must be guarded by an "
        "isinstance + non-empty-fields check so the legacy fallback "
        "still runs for non-drift errors."
    )

    # Legacy branch survives.
    assert "builder error" in src, (
        "Round 65 / C-1: the legacy 'builder error' fallback paragraph "
        "must still be present for non-drift ValueError instances and "
        "other exception kinds."
    )
