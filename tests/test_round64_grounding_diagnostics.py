"""Round 64 / Phase 3 (B5) regression: grounding-failure diagnostics rollup.

Build 36 manual acceptance produced 11 rejected customer narratives out
of 28 in the Brian Frazier / Contact Center comprehensive report (per
``QUALITY_AUDIT.md`` Round 64 handoff).  The R16 / R27
``ai_narrative_validator`` did its job -- it correctly substituted
``GROUNDING_FAILURE_PLACEHOLDER`` for every ungrounded narrative -- but
the operator had no per-report rollup of which customers / failure
codes / first-offending tokens tripped each rejection.  The failure
mode was invisible for the next round.

This test pins the Round 64 fix:

1.  ``_r64_record_grounding_outcome`` mutates ``status['grounding_diagnostics']``
    so the running-reports tile and the persisted ``analysis_status.json``
    carry an honest rollup.
2.  Customer names are routed through ``_id_digest`` so the persisted
    payload does not echo PII verbatim.
3.  Records are bounded (``_R64_MAX_GROUNDING_RECORDS``) so a
    pathological run with thousands of customers cannot inflate the
    status file unbounded.
4.  The ``rate`` field is a true rejection ratio (rejected / total),
    not a raw rejection count.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture(scope="module")
def app_simple_module():
    return importlib.import_module("app_simple")


@pytest.fixture
def fresh_status() -> dict:
    """A minimal status dict shaped like ``run_comprehensive_analysis`` builds."""
    return {
        "analysis_id": "Comprehensive_Brian_Frazier_All_Contact_Center_90d_1777639146",
        "manager": "Brian Frazier",
        "tech": "Contact Center",
        "days": 90,
        "status": "running",
        "progress": 75,
    }


# ---------------------------------------------------------------------------
# Container initialisation
# ---------------------------------------------------------------------------

def test_init_grounding_diagnostics_creates_default_shape(app_simple_module, fresh_status) -> None:
    diag = app_simple_module._r64_init_grounding_diagnostics(fresh_status)
    assert diag is fresh_status["grounding_diagnostics"], (
        "Helper must mutate the status dict in place (callers rely on it)"
    )
    assert diag["rejection_summary"] == {"rejected": 0, "total": 0, "rate": 0.0}
    assert diag["rejection_records"] == []
    assert diag["max_records"] == app_simple_module._R64_MAX_GROUNDING_RECORDS


def test_init_grounding_diagnostics_is_idempotent(app_simple_module, fresh_status) -> None:
    diag_first = app_simple_module._r64_init_grounding_diagnostics(fresh_status)
    diag_second = app_simple_module._r64_init_grounding_diagnostics(fresh_status)
    assert diag_first is diag_second, (
        "Repeated init must NOT clobber an existing diagnostics container "
        "-- otherwise per-customer recording would lose the running tally."
    )


# ---------------------------------------------------------------------------
# Per-narrative recording
# ---------------------------------------------------------------------------

def test_record_grounding_success_increments_total_only(app_simple_module, fresh_status) -> None:
    app_simple_module._r64_record_grounding_outcome(
        fresh_status,
        customer_name="Acme Corp",
        narrative_length=842,
        failures=None,
        sample_offending=None,
        rejected=False,
    )
    diag = fresh_status["grounding_diagnostics"]
    assert diag["rejection_summary"] == {"rejected": 0, "total": 1, "rate": 0.0}
    assert diag["rejection_records"] == []


def test_record_grounding_rejection_appends_record_with_digested_name(app_simple_module, fresh_status) -> None:
    app_simple_module._r64_record_grounding_outcome(
        fresh_status,
        customer_name="Acme Corp",
        narrative_length=1024,
        failures=["entities_invented", "claims_unsupported"],
        sample_offending={"entities_invented": "Globex Inc"},
        rejected=True,
    )
    diag = fresh_status["grounding_diagnostics"]
    assert diag["rejection_summary"]["rejected"] == 1
    assert diag["rejection_summary"]["total"] == 1
    assert diag["rejection_summary"]["rate"] == 1.0
    assert len(diag["rejection_records"]) == 1
    record = diag["rejection_records"][0]
    assert record["customer_digest"]
    assert "Acme Corp" not in record["customer_digest"], (
        "Customer name must be digested, not echoed verbatim, to keep "
        "the persisted status payload free of PII."
    )
    assert record["narrative_length"] == 1024
    assert record["failure_codes"] == ["claims_unsupported", "entities_invented"]
    assert record["first_offending_token"] == "Globex Inc"
    assert record["scope"] == "customer"


def test_record_grounding_rate_reflects_true_ratio_after_mixed_outcomes(app_simple_module, fresh_status) -> None:
    """Pin the ``rate`` field as ``rejected / total`` rather than a raw count.

    Build 36's 11/28 result must surface as ``rate = 0.393`` so the operator
    sees an honest ratio rather than an absolute number that scales with
    portfolio size.
    """
    # 11 rejections + 17 successes == 28 total (mirrors Build 36's actual
    # comprehensive-report outcome on the Brian Frazier / Contact Center
    # portfolio).
    for i in range(11):
        app_simple_module._r64_record_grounding_outcome(
            fresh_status,
            customer_name=f"Customer{i}",
            narrative_length=500,
            failures=["claims_unsupported"],
            sample_offending={"claims_unsupported": "97%"},
            rejected=True,
        )
    for i in range(17):
        app_simple_module._r64_record_grounding_outcome(
            fresh_status,
            customer_name=f"OkCustomer{i}",
            narrative_length=500,
            failures=None,
            sample_offending=None,
            rejected=False,
        )
    diag = fresh_status["grounding_diagnostics"]
    assert diag["rejection_summary"]["rejected"] == 11
    assert diag["rejection_summary"]["total"] == 28
    assert diag["rejection_summary"]["rate"] == round(11 / 28, 3)


def test_record_grounding_records_are_bounded(app_simple_module, fresh_status) -> None:
    """A pathological run must not blow up ``analysis_status.json`` size."""
    cap = app_simple_module._R64_MAX_GROUNDING_RECORDS
    for i in range(cap + 25):
        app_simple_module._r64_record_grounding_outcome(
            fresh_status,
            customer_name=f"Customer{i}",
            narrative_length=500,
            failures=["claims_unsupported"],
            sample_offending={"claims_unsupported": f"sample-{i}"},
            rejected=True,
        )
    diag = fresh_status["grounding_diagnostics"]
    assert diag["rejection_summary"]["rejected"] == cap + 25, (
        "Summary count must keep climbing even when records are capped"
    )
    assert len(diag["rejection_records"]) == cap, (
        f"Records must be bounded to {cap} so the persisted payload stays small"
    )


def test_record_grounding_handles_portfolio_scope(app_simple_module, fresh_status) -> None:
    app_simple_module._r64_record_grounding_outcome(
        fresh_status,
        customer_name="__portfolio__/Brian Frazier",
        narrative_length=2048,
        failures=["entities_invented"],
        sample_offending={"entities_invented": "Vandalay Industries"},
        rejected=True,
        scope="portfolio",
    )
    record = fresh_status["grounding_diagnostics"]["rejection_records"][0]
    assert record["scope"] == "portfolio"


def test_record_grounding_first_offending_token_is_truncated(app_simple_module, fresh_status) -> None:
    long_sample = "A" * 200
    app_simple_module._r64_record_grounding_outcome(
        fresh_status,
        customer_name="Acme",
        narrative_length=100,
        failures=["claims_unsupported"],
        sample_offending={"claims_unsupported": long_sample},
        rejected=True,
    )
    record = fresh_status["grounding_diagnostics"]["rejection_records"][0]
    assert len(record["first_offending_token"]) <= 80, (
        "First offending token must be capped at 80 chars to keep the "
        "diagnostics payload small."
    )


def test_record_grounding_handles_missing_status_gracefully(app_simple_module) -> None:
    """A non-dict status (defensive contract) must not raise."""
    # Should be a no-op rather than an AttributeError.
    app_simple_module._r64_record_grounding_outcome(
        None,  # type: ignore[arg-type]
        customer_name="Acme",
        narrative_length=10,
        failures=None,
        sample_offending=None,
        rejected=True,
    )


def test_record_grounding_first_offending_token_handles_none(app_simple_module, fresh_status) -> None:
    app_simple_module._r64_record_grounding_outcome(
        fresh_status,
        customer_name="Acme",
        narrative_length=100,
        failures=["claims_unsupported"],
        sample_offending=None,
        rejected=True,
    )
    record = fresh_status["grounding_diagnostics"]["rejection_records"][0]
    assert record["first_offending_token"] == ""


# ---------------------------------------------------------------------------
# first_offending_token helper -- separately for completeness
# ---------------------------------------------------------------------------

def test_first_offending_token_picks_dict_value(app_simple_module) -> None:
    assert app_simple_module._r64_first_offending_token({"k": "v1", "k2": "v2"}) == "v1"


def test_first_offending_token_skips_blank_values(app_simple_module) -> None:
    assert app_simple_module._r64_first_offending_token({"k": "", "k2": "real"}) == "real"


def test_first_offending_token_returns_empty_for_falsy(app_simple_module) -> None:
    assert app_simple_module._r64_first_offending_token(None) == ""
    assert app_simple_module._r64_first_offending_token({}) == ""
    assert app_simple_module._r64_first_offending_token("") == ""


def test_first_offending_token_truncates_string_input(app_simple_module) -> None:
    assert len(app_simple_module._r64_first_offending_token("x" * 200)) == 80
