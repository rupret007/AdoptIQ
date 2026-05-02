"""Round 66 / Pass 2 (B6) — per-customer Risk_Components sheet tests.

Pre-R66 the Comprehensive XLSX advertised a portfolio-level risk band
tile but never surfaced the per-customer component breakdown. The
Renewal XLSX has carried a single-customer ``Risk_Components`` tab
since Round 4; this builds the comprehensive cousin by iterating
``risk_profiles`` (already keyed on the full
``all_customers_comprehensive`` universe by line ~14481 in
``app_simple.py``) and emitting one row per customer with the
individual component scores from
``risk_scoring.compute_customer_risk_profile``.

The construction code path lives in ``app_simple.py`` immediately
before the ``write_excel_workbook`` call in the comprehensive flow
(after the R64 / B2 ``Action_Plans`` sheet block). These tests pin
the source-shape so an accidental refactor cannot silently drop the
per-customer breakdown again.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest


@pytest.fixture
def app_simple_source() -> str:
    """Read ``app_simple.py`` once for source-shape assertions."""
    repo_root = Path(__file__).resolve().parent.parent
    return (repo_root / "app_simple.py").read_text(encoding="utf-8")


def test_risk_components_block_is_present_in_comprehensive_flow(app_simple_source: str) -> None:
    """The comprehensive flow MUST construct a per-customer Risk_Components sheet.

    Pinned to the R66/B6 marker comment so a future refactor that
    drops the block will fail loudly here instead of silently
    shipping a Comprehensive XLSX without per-customer breakdown.
    """
    assert "Round 66 / Pass 2 (B6): per-customer ``Risk_Components``" in app_simple_source, (
        "R66/B6 source marker missing from app_simple.py"
    )
    assert "_r66_risk_rows" in app_simple_source, (
        "R66/B6 risk-rows accumulator variable missing"
    )
    assert 'all_sheets["Risk_Components"]' in app_simple_source, (
        "R66/B6 Risk_Components sheet assignment missing from all_sheets"
    )


def test_risk_components_block_runs_inside_comprehensive_excel_writer(app_simple_source: str) -> None:
    """The Risk_Components construction MUST sit inside the Excel writer block.

    The block must be inside the ``Write Excel file with all data``
    branch so it only runs for comprehensive reports — not the
    leader / renewal / compact flows that have their own writers.
    """
    write_excel_idx = app_simple_source.index("Write Excel file with all data")
    r66_b6_idx = app_simple_source.index("Round 66 / Pass 2 (B6): per-customer ``Risk_Components``")
    xlsx_path_idx = app_simple_source.index(
        "xlsx_path = write_excel_workbook(base, all_sheets,"
    )
    assert write_excel_idx < r66_b6_idx < xlsx_path_idx, (
        "R66/B6 block must sit between the Excel writer banner "
        "and the write_excel_workbook call"
    )


def test_risk_components_block_iterates_seven_canonical_components(app_simple_source: str) -> None:
    """All seven risk components from compute_customer_risk_profile MUST be surfaced."""
    canonical_components = [
        ("adoption_barriers", "Adoption_Barriers_Score"),
        ("support_cases", "Support_Cases_Score"),
        ("customer_pulse", "Customer_Pulse_Score"),
        ("action_plans", "Action_Plans_Score"),
        ("incidents", "Incidents_Score"),
        ("contract", "Contract_Score"),
        ("engagement", "Engagement_Score"),
    ]
    for ck, col in canonical_components:
        assert f'"{ck}"' in app_simple_source and f'"{col}"' in app_simple_source, (
            f"Component pair ({ck} -> {col}) missing from app_simple.py"
        )


def test_risk_components_block_sorts_by_score_desc_then_name_asc(app_simple_source: str) -> None:
    """Sort key MUST be (Risk_Score_0_100 DESC, Customer_Name ASC).

    Per the SSoT determinism rule: top-N sorts MUST include a
    stable name-based tiebreaker so ``make verify`` stays
    deterministic across runs. Otherwise downstream consumers see
    row-order flap on every regenerate.
    """
    block_start = app_simple_source.index("_r66_risk_rows.sort(")
    block_end = app_simple_source.index(")", block_start + 1000)
    sort_block = app_simple_source[block_start:block_end + 1]
    assert "Risk_Score_0_100" in sort_block, "Score component missing from sort key"
    assert "Customer_Name" in sort_block, "Name tiebreaker missing from sort key"
    score_pos = sort_block.index("Risk_Score_0_100")
    name_pos = sort_block.index("Customer_Name")
    assert score_pos < name_pos, "Score MUST come before name in sort key"
    assert "-(" in sort_block or "reverse=True" in sort_block, (
        "Score sort MUST be DESC (negation or reverse=True)"
    )


def test_risk_components_block_writes_provenance_row_when_no_profiles(app_simple_source: str) -> None:
    """Empty-profiles fallback MUST emit a single Data_Unavailable provenance row."""
    block_start = app_simple_source.index("Round 66 / Pass 2 (B6): per-customer ``Risk_Components``")
    block_end = app_simple_source.index("xlsx_path = write_excel_workbook(base, all_sheets,")
    block = app_simple_source[block_start:block_end]
    assert '"_adoptiq_provenance_row": True' in block, (
        "Provenance marker column missing from empty-fallback row"
    )
    assert '"AdoptIQ_Status": "EMPTY"' in block, (
        "AdoptIQ_Status=EMPTY missing from empty-fallback row"
    )
    assert "risk_scoring.compute_customer_risk_profile" in block, (
        "Provenance source citation missing from empty-fallback row"
    )


def test_risk_components_block_handles_non_dict_profile_safely(app_simple_source: str) -> None:
    """Non-dict profile values MUST be skipped (defense against malformed input)."""
    block_start = app_simple_source.index("Round 66 / Pass 2 (B6): per-customer ``Risk_Components``")
    block_end = app_simple_source.index("xlsx_path = write_excel_workbook(base, all_sheets,")
    block = app_simple_source[block_start:block_end]
    assert "if not isinstance(_r66_profile, dict):" in block, (
        "Profile dict-type guard missing"
    )
    assert "continue" in block, "continue statement missing for non-dict profile"


def test_risk_components_block_wraps_in_try_except(app_simple_source: str) -> None:
    """The whole block MUST be wrapped in try/except so a malformed risk_profile cannot abort the XLSX writer."""
    block_start = app_simple_source.index("Round 66 / Pass 2 (B6): per-customer ``Risk_Components``")
    block_end = app_simple_source.index("xlsx_path = write_excel_workbook(base, all_sheets,")
    block = app_simple_source[block_start:block_end]
    assert "try:" in block, "Construction block MUST be wrapped in try"
    assert "except Exception as _r66_risk_err:" in block, (
        "Construction block MUST catch with named exception variable"
    )
    assert "logger.debug(" in block, "Failure MUST be logged at debug level"
    try_idx = block.index("try:")
    except_idx = block.index("except Exception as _r66_risk_err:")
    assert try_idx < except_idx, "try MUST appear before except"


def test_risk_components_canonical_columns_match_compute_profile_keys() -> None:
    """The canonical component keys MUST match what compute_customer_risk_profile actually returns.

    This is a parity check — if risk_scoring.py adds a new component
    or renames an existing one, this test will fail and force a
    review of the Comprehensive Risk_Components schema.
    """
    from risk_scoring import compute_customer_risk_profile

    profile = compute_customer_risk_profile(
        customer_name="Test Customer",
        customer_ab=pd.DataFrame(),
        customer_csone=pd.DataFrame(),
        customer_pulse=pd.DataFrame(),
        customer_action_plans=pd.DataFrame(),
        customer_subs=pd.DataFrame(),
        ext_incidents=[],
    )
    components = profile.get("components", {})
    expected_keys = {
        "adoption_barriers",
        "support_cases",
        "customer_pulse",
        "action_plans",
        "incidents",
        "contract",
        "engagement",
    }
    actual_keys = set(components.keys())
    missing = expected_keys - actual_keys
    assert not missing, (
        f"compute_customer_risk_profile no longer returns expected components: {missing}"
    )


def test_risk_components_block_emits_top_risk_factor_column(app_simple_source: str) -> None:
    """Top_Risk_Factor column MUST be populated from risk_factors[0]."""
    block_start = app_simple_source.index("Round 66 / Pass 2 (B6): per-customer ``Risk_Components``")
    block_end = app_simple_source.index("xlsx_path = write_excel_workbook(base, all_sheets,")
    block = app_simple_source[block_start:block_end]
    assert '"Top_Risk_Factor"' in block, "Top_Risk_Factor column missing"
    assert '_r66_factors[0]' in block, "Top factor MUST be the first risk_factors entry"
    assert ":480]" in block, "Top factor MUST be capped at 480 chars (matches R47 details cap)"


def test_risk_components_block_runs_before_write_excel_workbook_call(app_simple_source: str) -> None:
    """Block MUST emit before write_excel_workbook so the sheet is in all_sheets at write time."""
    block_idx = app_simple_source.index('all_sheets["Risk_Components"] = pd.DataFrame(_r66_risk_rows)')
    write_idx = app_simple_source.index("xlsx_path = write_excel_workbook(base, all_sheets,")
    assert block_idx < write_idx, (
        "Risk_Components assignment MUST precede write_excel_workbook call"
    )


def test_risk_components_block_does_not_clobber_action_plans(app_simple_source: str) -> None:
    """The block MUST sit AFTER the R64/B2 Action_Plans block (sequential)."""
    ap_idx = app_simple_source.index('all_sheets["Action_Plans"] = pd.DataFrame([{')
    rc_idx = app_simple_source.index("Round 66 / Pass 2 (B6): per-customer ``Risk_Components``")
    assert ap_idx < rc_idx, (
        "B6 Risk_Components MUST run AFTER B2 Action_Plans, not before"
    )


def test_risk_components_block_uses_expected_band_value_passthrough(app_simple_source: str) -> None:
    """Risk_Band column MUST come from profile.risk_band (no recomputation)."""
    block_start = app_simple_source.index("Round 66 / Pass 2 (B6): per-customer ``Risk_Components``")
    block_end = app_simple_source.index("xlsx_path = write_excel_workbook(base, all_sheets,")
    block = app_simple_source[block_start:block_end]
    assert '_r66_profile.get("risk_band")' in block, (
        "Risk_Band MUST be read directly from profile, not recomputed"
    )
    assert '_r66_profile.get("risk_score_0_100")' in block, (
        "Risk_Score_0_100 MUST be read directly from profile"
    )
