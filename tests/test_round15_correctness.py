"""Round 15 / Phase 5 -- correctness adversarial regression tests.

Each test pins a specific Round-15 correctness fix so a future
refactor can't quietly regress it.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

import report_export_styling as styling


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (_REPO_ROOT / path).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Phase 5.1 -- UTC anchoring of the default workbook filename suffix.
# ---------------------------------------------------------------------------


def test_phase_5_1_workbook_default_filename_uses_utc_z_suffix():
    """Round 15 / Phase 5.1 regression guard.

    ``write_excel_workbook(sheets_dict)`` (no path supplied) used to
    stamp ``datetime.now()`` (host-local clock) into the default
    filename, so two operators in different regions on the same UTC
    second got *different* filenames.  Pin the marker comment so the
    UTC anchoring + ``Z`` suffix can't quietly regress.
    """
    contents = _read("adoptiq_backend.py")
    assert "Round 15 / Phase 5.1" in contents
    # Verify the new UTC-anchored format is what's actually emitted.
    assert "datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')" in contents
    assert "Z\"" in contents or "}Z\"" in contents


# ---------------------------------------------------------------------------
# Phase 5.2 -- KPI formatter is non-finite safe.
# ---------------------------------------------------------------------------


def test_phase_5_2_format_kpi_nan_is_dashed():
    """``float('nan')`` previously raised ``ValueError`` from the
    ``int(value)`` call -- crashing the entire Summary write.
    """
    assert styling._format_kpi(float("nan")) == "--"


def test_phase_5_2_format_kpi_infinity_is_dashed():
    """``float('inf')`` / ``float('-inf')`` previously raised
    ``OverflowError`` from ``int(value)`` -- same crash path as NaN.
    """
    assert styling._format_kpi(float("inf")) == "--"
    assert styling._format_kpi(float("-inf")) == "--"


def test_phase_5_2_format_kpi_zero_still_renders_as_zero():
    """Defensive zero handling must NOT regress to ``"--"``."""
    assert styling._format_kpi(0) == "0"
    assert styling._format_kpi(0.0) == "0"


def test_phase_5_2_format_kpi_bool_renders_as_yes_no():
    """Round 15 / Phase 5.2 -- ``bool`` is a subclass of ``int``.
    Render explicitly so a stray ``True`` doesn't surface as the
    integer ``"1"`` in a count cell.
    """
    assert styling._format_kpi(True) == "Yes"
    assert styling._format_kpi(False) == "No"


def test_phase_5_2_format_kpi_integer_floats_lose_decimals():
    """A canonical-metrics call that returns ``42.0`` should render
    as ``"42"``, not ``"42.0"``."""
    assert styling._format_kpi(42.0) == "42"
    assert styling._format_kpi(1234567.0) == "1,234,567"


def test_phase_5_2_format_kpi_non_integer_floats_keep_one_decimal():
    """Real (non-integer) floats keep a single decimal place."""
    assert styling._format_kpi(8.55) == "8.6"
    assert styling._format_kpi(1234.5) == "1,234.5"


def test_phase_5_2_marker_in_report_export_styling():
    contents = _read("report_export_styling.py")
    assert "Round 15 / Phase 5.2" in contents


# ---------------------------------------------------------------------------
# Phase 5.3 -- ``count_customers`` is keyword-only; callers must comply.
# ---------------------------------------------------------------------------


def test_phase_5_3_summary_rows_actually_emit_customer_count():
    """Round 15 / Phase 5.3 regression guard.

    ``cm.count_customers`` is keyword-only.  Before this fix the
    Phase-2 wiring passed the frames positionally, raising
    ``TypeError`` that was silently swallowed by
    ``_safe_canonical_call``, leaving "Customers in portfolio" as
    ``"--"`` even when the frames clearly carried customer rows.
    """
    ab = pd.DataFrame(
        {
            "ID": ["a1", "a2", "a3"],
            "BU_NAME": ["Acme", "Beta", "Acme"],
        }
    )
    csone = pd.DataFrame({"Customer Name": ["Acme", "Gamma"]})
    rows = styling.build_summary_rows(
        {"AB_Detail_All": ab, "CSOne_Detail_All": csone},
        None,
    )
    rowmap = dict(rows)
    # Acme + Beta + Gamma == 3 unique customers (Acme dedups across frames).
    assert rowmap.get("Customers in portfolio") == "3", (
        f"Round 15 / Phase 5.3 regression: expected '3' customers, "
        f"got {rowmap.get('Customers in portfolio')!r}.  "
        f"Likely the keyword-only call site regressed back to positional."
    )


def test_phase_5_3_summary_rows_handle_missing_frames():
    """Defensive: when no frames are supplied at all, the customer
    count must render as ``"0"`` (count of zero) rather than blowing
    up.  This is the empty-portfolio case and must remain stable.
    """
    rows = styling.build_summary_rows({}, None)
    rowmap = dict(rows)
    # No frames -> count is zero.
    assert rowmap.get("Customers in portfolio") == "0"


def test_phase_5_3_marker_in_report_export_styling():
    contents = _read("report_export_styling.py")
    assert "Round 15 / Phase 5.3" in contents
