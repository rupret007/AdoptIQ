"""Round 48 / F-RP-WARNING-COUNT-WRONG regression tests.

Pin that the renewal Excel ``Report_Info`` builder populates
``Partial_Data_Warning_Count`` from the actual harvested
``df.attrs['fetch_error']`` annotations rather than always reading
zero.  The audit baseline (run 1777445582) showed
``Partial_Data_Warning_Count = 0`` even though two real
``schema_drift`` warnings existed on the renewal pulse and AB
frames.

These tests focus on the harvest step's source-level structure
(the ``run_customer_renewal_analysis`` ``_r48_renewal_pdw`` block)
because the full renewal analysis function requires Snowflake +
network and cannot be exercised end-to-end in CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest


_APP_SIMPLE_PATH = Path(__file__).resolve().parent.parent / "app_simple.py"


@pytest.fixture(scope="module")
def app_simple_source() -> str:
    return _APP_SIMPLE_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Harvest block must exist AND be wired to the right datasets
# ---------------------------------------------------------------------------


def test_round48_fix_anchor_present(app_simple_source: str):
    assert "F-RP-WARNING-COUNT-WRONG" in app_simple_source, (
        "Round 48 fix anchor F-RP-WARNING-COUNT-WRONG missing from "
        "app_simple.py; the renewal Excel will continue to report "
        "Partial_Data_Warning_Count = 0 even when warnings exist"
    )


def test_round48_renewal_pdw_harvest_block_present(app_simple_source: str):
    """The harvest loop must scan the per-customer frames that
    ``data_contracts.validate_against_contract`` annotates."""

    assert "_r48_renewal_pdw" in app_simple_source, (
        "Renewal partial_data_warnings harvest local "
        "(_r48_renewal_pdw) missing"
    )


@pytest.mark.parametrize(
    "dataset_label",
    [
        "adoption_barriers",
        "csone_tac_cases",
        "customer_pulse",
        "action_plans",
        "success_priorities",
        "team_subs",
    ],
)
def test_round48_harvest_covers_each_renewal_dataset(
    app_simple_source: str, dataset_label: str,
):
    """Each renewal-relevant frame must appear as a harvest source.
    If a future refactor renames or drops one of these, this test
    flags it so the Excel ledger does not silently lose a class
    of warning.
    """

    assert f'"{dataset_label}"' in app_simple_source, (
        f"Renewal partial_data_warnings harvest does not enumerate "
        f"the '{dataset_label}' dataset"
    )


def test_round48_harvest_persists_to_analysis_status(app_simple_source: str):
    """The harvested entries must be appended onto
    ``analysis_status[analysis_id]['partial_data_warnings']`` and
    ``save_analysis_status()`` must be called so the Excel writer
    (which reads from that list) sees them.  Use loose substrings
    so a defensive refactor that introduces helpers still passes.
    """

    assert "_persisted_pdw = _persisted.setdefault('partial_data_warnings'" in app_simple_source, (
        "Renewal harvest does not setdefault the persisted "
        "partial_data_warnings list"
    )
    # The save_analysis_status() call happens inside the lock block
    # right after the persisted list is appended.
    harvest_pos = app_simple_source.find("_r48_renewal_pdw: list = []")
    save_pos = app_simple_source.find(
        "save_analysis_status()", harvest_pos
    )
    assert save_pos != -1, (
        "save_analysis_status() not called after R48 harvest; "
        "Excel writer will not see freshly-harvested warnings"
    )
    assert save_pos - harvest_pos < 4000, (
        "save_analysis_status() is too far from the R48 harvest "
        "block; verify the fix is wired correctly"
    )


# ---------------------------------------------------------------------------
# Excel writer still pulls the count from the same persisted list
# ---------------------------------------------------------------------------


def test_round48_excel_writer_reads_persisted_pdw(app_simple_source: str):
    """The renewal ``Report_Info`` builder must read its
    ``Partial_Data_Warning_Count`` value from the persisted list,
    not from a hardcoded zero.

    Round 73 / F6: the column key migrated from ``Field`` to ``Item``
    as part of the Item/Value canonical schema standardisation.  The
    harvest semantics are unchanged -- only the column name moved.
    """

    assert (
        "{'Item': 'Partial_Data_Warning_Count', 'Value': str(len(_ren_pdw))}"
        in app_simple_source
    ), (
        "Renewal Report_Info Partial_Data_Warning_Count row no longer "
        "uses len(_ren_pdw); the R48 harvest fix is unwired (or the R73/F6 "
        "Item key migration was reverted)"
    )


def test_round48_harvest_uses_redact_helper(app_simple_source: str):
    """Each harvested error string must flow through
    ``_redact_partial_warning_error`` so URLs / hosts / paths are
    stripped before reaching the user-visible Excel cell.
    Round 13 / Phase 4.5 hardening must be preserved by R48.
    """

    # The harvest block contains one such call -- pin it explicitly.
    assert (
        "'error': _redact_partial_warning_error(_err)" in app_simple_source
    ), (
        "Renewal R48 harvest does not run errors through "
        "_redact_partial_warning_error; PII / URL leak risk"
    )
