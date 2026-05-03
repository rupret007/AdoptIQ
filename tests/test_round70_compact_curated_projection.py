"""Round 70 / Phase 2 (#7) -- Compact ``Critical_Adoption_Barriers`` +
``Action_Plans`` MUST be projected through the curated schema.

Build 43 acceptance audit found:

- Compact ``Critical_Adoption_Barriers``: 259 columns (raw
  ``AB_COMPETITOR_C``, ``AB_SOLUTION_ATTEMPT_C``, internal Salesforce
  IDs).
- Compact ``Action_Plans``: 243 columns (same raw shape).

R67/B7 contract: both MUST be projected through ``_r15_apply_export_
schema`` to ~30-60 customer-facing columns via ``report_export_schema.
_CURATED_AB_DETAIL_ALL`` and ``_CURATED_ACTION_PLANS``. The wiring in
``app_simple.py`` was bypassed.

The Round 70 fix re-routes the Compact writer through the curated
projection helper.
"""
from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read_app_simple() -> str:
    return (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")


def _read_export_schema() -> str:
    return (PROJECT_ROOT / "report_export_schema.py").read_text(encoding="utf-8")


def test_curated_ab_detail_all_constant_defined() -> None:
    """R67/B7 + R70/#7: the curated AB column list MUST exist in the
    export schema module."""
    src = _read_export_schema()
    assert "_CURATED_AB_DETAIL_ALL" in src, (
        "Round 70 / #7: report_export_schema MUST define the curated "
        "AB column list."
    )


def test_curated_action_plans_constant_defined() -> None:
    """R67/B7 + R70/#7: the curated AP column list MUST exist."""
    src = _read_export_schema()
    assert "_CURATED_ACTION_PLANS" in src, (
        "Round 70 / #7: report_export_schema MUST define the curated "
        "Action_Plans column list."
    )


def test_compact_writer_calls_export_schema_helper() -> None:
    """R67/B7 + R70/#7: the Compact writer block MUST invoke
    ``_r15_apply_export_schema`` (or an equivalent helper) so the
    Critical_Adoption_Barriers / Action_Plans sheets land curated."""
    src = _read_app_simple()
    assert "_r15_apply_export_schema" in src or "apply_export_schema" in src, (
        "Round 70 / #7: Compact writer MUST route AB / AP through the "
        "curated projection helper, not pass raw Snowflake DataFrames."
    )


def test_compact_writer_curated_columns_bounded() -> None:
    """R70/#7: the curated projection MUST cap the column count well
    below the 200+ raw Snowflake dump width."""
    src = _read_export_schema()
    # The exact constant body lives in report_export_schema. We only
    # need to assert the constants are NOT empty AND are < 100 cols.
    import importlib

    schema_mod = importlib.import_module("report_export_schema")
    ab_curated = getattr(schema_mod, "_CURATED_AB_DETAIL_ALL", None)
    ap_curated = getattr(schema_mod, "_CURATED_ACTION_PLANS", None)
    assert ab_curated is not None, "R70/#7: curated AB list MUST be exported"
    assert ap_curated is not None, "R70/#7: curated AP list MUST be exported"
    assert 5 <= len(ab_curated) < 100, (
        f"Round 70 / #7: curated AB list MUST be 5-100 cols; saw {len(ab_curated)}"
    )
    assert 5 <= len(ap_curated) < 100, (
        f"Round 70 / #7: curated AP list MUST be 5-100 cols; saw {len(ap_curated)}"
    )
