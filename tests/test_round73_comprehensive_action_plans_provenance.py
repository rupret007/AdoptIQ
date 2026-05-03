"""Round 73 / Phase 1 (F2) -- Comprehensive Action_Plans empty-state
preserves the R64/B2 provenance row through ``apply_export_schema``.

Build 46 acceptance audit found that the Comprehensive XLSX's
``Action_Plans`` sheet rendered as ``1 row x 1 col with A1=None``
instead of the canonical R64/B2 provenance triple
(``_adoptiq_provenance_row=True`` / ``AdoptIQ_Status='EMPTY'`` /
``AdoptIQ_Source='CSConsole+Snowflake'``).

Root cause was NOT in the writer -- ``app_simple.py`` correctly
constructs the provenance row at ~L17234. The issue was downstream in
``write_excel_workbook`` (``adoptiq_backend.py``) which routes EVERY
sheet through ``_r15_apply_export_schema(df_copy, sheet_name=name)``
before write. For ``Action_Plans`` the curated allow-list
``_CURATED_ACTION_PLANS`` does not contain any of the provenance
metadata columns (``_adoptiq_provenance_row``, ``AdoptIQ_Status``,
``AdoptIQ_Source``, ``AdoptIQ_Provenance``, ``AdoptIQ_Message``), so
``filter_columns`` returned an empty list and ``apply_export_schema``
fell back to ``df.iloc[:, 0:0]`` -- a 1-row / 0-col DataFrame that
xlsxwriter rendered as a single empty cell.

The fix: ``apply_export_schema`` short-circuits the projection when
the input DataFrame carries the ``_adoptiq_provenance_row`` marker
column. The marker means "this is a R64/B2 fallback diagnostic row,
preserve it verbatim so downstream consumers can read the honest
provenance instead of seeing a missing sheet".

This pin covers:

* The schema fix itself -- a 1-row provenance frame survives the
  projection unchanged.
* The Comprehensive Action_Plans writer fallback at ~L17234 still
  carries the R65 provenance triple (source-shape pin so a future
  refactor that reverts the writer fails loud).
* End-to-end: a synthetic empty ``Action_Plans`` frame, routed
  through ``write_excel_workbook``, produces a sheet whose first row
  has ``_adoptiq_provenance_row=True`` and ``AdoptIQ_Status='EMPTY'``.
* ``canonical_metrics.count_open_action_plans`` still returns 0 when
  the only AP row is a provenance marker (so the Summary KPI does
  not lie).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Schema-level fix: provenance rows survive apply_export_schema
# ---------------------------------------------------------------------------


def test_apply_export_schema_preserves_provenance_marker_row() -> None:
    """A DataFrame carrying ``_adoptiq_provenance_row`` MUST survive
    ``apply_export_schema`` projection unchanged. Pre-R73 this row was
    silently reduced to a 0-col frame because none of its meta columns
    appeared in the per-sheet curated allow-list.
    """

    pd = pytest.importorskip("pandas")
    from report_export_schema import apply_export_schema  # noqa: PLC0415

    provenance_df = pd.DataFrame([{
        "_adoptiq_provenance_row": True,
        "AdoptIQ_Status": "EMPTY",
        "AdoptIQ_Source": "CSConsole+Snowflake",
        "AdoptIQ_Provenance": "csconsole+snowflake",
        "AdoptIQ_Message": "No action plans found for this scope.",
    }])

    out = apply_export_schema(provenance_df, sheet_name="Action_Plans")

    assert out.shape[0] == 1, (
        f"Round 73 / F2: provenance row dropped by projection; "
        f"got shape={out.shape!r}"
    )
    assert "_adoptiq_provenance_row" in out.columns, (
        "Round 73 / F2: marker column lost in projection"
    )
    assert "AdoptIQ_Status" in out.columns, (
        "Round 73 / F2: AdoptIQ_Status column lost in projection"
    )
    assert "AdoptIQ_Source" in out.columns, (
        "Round 73 / F2: AdoptIQ_Source column lost in projection"
    )
    assert bool(out.iloc[0]["_adoptiq_provenance_row"]) is True
    assert str(out.iloc[0]["AdoptIQ_Status"]) == "EMPTY"
    assert str(out.iloc[0]["AdoptIQ_Source"]) == "CSConsole+Snowflake"


def test_apply_export_schema_does_not_short_circuit_normal_rows() -> None:
    """The R73 short-circuit must NOT fire for a normal AP frame --
    real AP data must still be projected through the curated allow-list.
    """

    pd = pytest.importorskip("pandas")
    from report_export_schema import apply_export_schema, _CURATED_ACTION_PLANS  # noqa: PLC0415

    real_ap_df = pd.DataFrame([
        {
            "ID": "AP001",
            "NAME": "Test AP 1",
            "BU_NAME": "Customer A",
            "SUBJECT_C": "Test subject 1",
            "DELETE_FLAG": "N",  # SF plumbing column -- must be dropped
            "EDWSF_INTERNAL_ID": 12345,  # ETL column -- must be dropped
        },
        {
            "ID": "AP002",
            "NAME": "Test AP 2",
            "BU_NAME": "Customer B",
            "SUBJECT_C": "Test subject 2",
            "DELETE_FLAG": "N",
            "EDWSF_INTERNAL_ID": 67890,
        },
    ])

    out = apply_export_schema(real_ap_df, sheet_name="Action_Plans")

    assert out.shape[0] == 2, (
        f"Round 73 / F2: real AP rows lost; got shape={out.shape!r}"
    )
    # Curated allow-list must apply -- SF plumbing dropped, curated columns kept.
    assert "DELETE_FLAG" not in out.columns, (
        "Round 73 / F2: SF plumbing leaked through projection -- "
        "the short-circuit fired for non-provenance frames"
    )
    # The R45/Phase 5 friendly-header rename runs AFTER the curated
    # projection -- raw ``BU_NAME`` becomes ``Customer Name`` and
    # ``SUBJECT_C`` becomes ``Subject``.  Either form (raw or friendly)
    # is acceptable proof that the curated projection ran.
    assert "ID" in out.columns and "NAME" in out.columns
    assert "BU_NAME" in out.columns or "Customer Name" in out.columns, (
        "Round 73 / F2: customer-name column missing after projection"
    )


def test_apply_export_schema_short_circuit_works_for_unknown_sheet_name() -> None:
    """The provenance short-circuit must fire regardless of sheet_name --
    it is a property of the FRAME (carries the marker column), not the
    sheet. This protects a future R64/B2-style sheet that has no
    curated allow-list entry yet.
    """

    pd = pytest.importorskip("pandas")
    from report_export_schema import apply_export_schema  # noqa: PLC0415

    provenance_df = pd.DataFrame([{
        "_adoptiq_provenance_row": True,
        "AdoptIQ_Status": "EMPTY",
        "AdoptIQ_Source": "future_sheet_source",
    }])

    # No curated entry for this sheet name -- pre-R73 this would still
    # have lost the marker column to ``is_internal_column`` (the
    # leading-underscore prefix denylist).
    out = apply_export_schema(provenance_df, sheet_name="Future_Provenance_Sheet")

    assert out.shape[0] == 1
    assert "_adoptiq_provenance_row" in out.columns


def test_apply_export_schema_short_circuit_handles_missing_columns_attr() -> None:
    """Defensive: a non-DataFrame input must not crash the projection
    pipeline -- the R73 short-circuit must fail closed (continue with
    normal projection) rather than abort.
    """

    pd = pytest.importorskip("pandas")
    from report_export_schema import apply_export_schema  # noqa: PLC0415

    # Pass a plain list of dicts -- apply_export_schema coerces it to a
    # DataFrame internally before the short-circuit check, so this MUST
    # still work normally.
    rows = [{"ID": "X", "NAME": "Y", "BU_NAME": "Z"}]
    out = apply_export_schema(rows, sheet_name="Action_Plans")
    assert out is not None
    assert out.shape[0] == 1


# ---------------------------------------------------------------------------
# Source-shape pin: the comprehensive writer's R65 fallback is intact
# ---------------------------------------------------------------------------


def test_comprehensive_writer_carries_r65_provenance_fallback() -> None:
    """Source-shape pin: ``app_simple.py`` Comprehensive ``Action_Plans``
    fallback must construct the canonical R64/B2 provenance triple --
    a future refactor that drops the assignment fails loud here.
    """

    body = (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")

    # The fallback assignment block must contain the canonical triple.
    assert '"_adoptiq_provenance_row": True' in body, (
        "Round 73 / F2: Comprehensive Action_Plans fallback missing "
        "_adoptiq_provenance_row=True marker"
    )
    assert '"AdoptIQ_Status": "EMPTY"' in body, (
        "Round 73 / F2: Comprehensive Action_Plans fallback missing "
        "AdoptIQ_Status='EMPTY' marker"
    )
    assert '"AdoptIQ_Source": "CSConsole+Snowflake"' in body, (
        "Round 73 / F2: Comprehensive Action_Plans fallback missing "
        "AdoptIQ_Source='CSConsole+Snowflake' marker"
    )


# ---------------------------------------------------------------------------
# End-to-end: produce a real workbook and read the sheet back
# ---------------------------------------------------------------------------


def test_write_excel_workbook_preserves_action_plans_provenance_row(tmp_path) -> None:
    """End-to-end: route an empty AP frame's provenance fallback
    through ``write_excel_workbook`` and assert the produced
    ``Action_Plans`` sheet carries the R64/B2 triple verbatim.
    """

    pytest.importorskip("xlsxwriter")
    pytest.importorskip("openpyxl")
    pd = pytest.importorskip("pandas")
    from adoptiq_backend import write_excel_workbook  # noqa: PLC0415

    # Synthetic comprehensive sheets dict mirroring the empty-AP shape
    # the Build 46 audit observed.  Action_Plans = 1-row provenance.
    sheets = {
        "AB_Detail_All": pd.DataFrame([{"id": "AB001", "subject": "test barrier"}]),
        "Action_Plans": pd.DataFrame([{
            "_adoptiq_provenance_row": True,
            "AdoptIQ_Status": "EMPTY",
            "AdoptIQ_Source": "CSConsole+Snowflake",
            "AdoptIQ_Provenance": "csconsole+snowflake",
            "AdoptIQ_Message": (
                "No action plans found for this scope. Both CSConsole "
                "and Snowflake returned zero rows."
            ),
        }]),
    }

    base = tmp_path / "round73_f2_smoke"
    out_path = write_excel_workbook(
        str(base),
        sheets,
        manager="Test Manager",
        technology="Test Tech",
        days=90,
    )
    out = Path(out_path)
    assert out.exists()

    # Read the produced Action_Plans sheet and assert the R64/B2 row
    # survived the projection + write cycle.
    df = pd.read_excel(out, sheet_name="Action_Plans")
    assert df.shape[0] == 1, (
        f"Round 73 / F2: produced Action_Plans sheet has wrong row count; "
        f"got shape={df.shape!r}"
    )
    assert "_adoptiq_provenance_row" in df.columns, (
        f"Round 73 / F2: produced sheet missing provenance marker column; "
        f"got columns={list(df.columns)!r}"
    )
    assert "AdoptIQ_Status" in df.columns
    assert "AdoptIQ_Source" in df.columns
    # The marker round-trips through xlsx as either True/Truthy or 1
    # depending on dtype inference -- accept any truthy value.
    assert bool(df.iloc[0]["_adoptiq_provenance_row"])
    assert str(df.iloc[0]["AdoptIQ_Status"]) == "EMPTY"
    assert str(df.iloc[0]["AdoptIQ_Source"]) == "CSConsole+Snowflake"


# ---------------------------------------------------------------------------
# Summary KPI honesty: count_open_action_plans skips the marker row
# ---------------------------------------------------------------------------


def test_count_open_action_plans_ignores_provenance_row() -> None:
    """The R64/B2 contract requires
    ``canonical_metrics.count_open_action_plans`` to skip provenance
    marker rows so the Summary KPI reads ``0`` (not ``1``) when the
    only row in the Action_Plans sheet is the EMPTY fallback. This was
    pinned in R65 but re-asserted here to lock the cross-module contract
    in the same regression suite.
    """

    pd = pytest.importorskip("pandas")
    import canonical_metrics as cm  # noqa: PLC0415

    provenance_only = pd.DataFrame([{
        "_adoptiq_provenance_row": True,
        "AdoptIQ_Status": "EMPTY",
        "AdoptIQ_Source": "CSConsole+Snowflake",
    }])

    # Signature: count_open_action_plans(ab_df, ap_df=None) -- the AB
    # frame is the legacy R62/B fallback; the new R64/B2 ap_df is the
    # canonical path.  Pass an empty AB frame so only the provenance
    # ap_df drives the count.
    open_count = cm.count_open_action_plans(
        ab_df=pd.DataFrame(), ap_df=provenance_only,
    )
    assert open_count == 0, (
        f"Round 73 / F2: count_open_action_plans must ignore provenance "
        f"marker rows; got {open_count}"
    )
