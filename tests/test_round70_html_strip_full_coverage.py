"""Round 70 / Phase 3 (#10) -- HTML strip allow-list + per-writer coverage.

Build 43 acceptance audit found HTML markup leaking into 6 sheets across
4 reports:

- Comprehensive ``CSOne_Detail_All`` (covered by R67/B4) +
  ``External_Incidents`` (NOT covered).
- Compact ``All_Support_Cases`` (NOT covered) -- 4 cells with raw
  ``persona : Admin\\nOrgType : Customer ...`` HTML-encoded entities.
- Renewal ``Customer_Support_Cases`` (NOT covered) -- 4 cells with the
  same shape.
- Leader ``TAC_Cases`` (NOT covered) + ``External_Incidents`` (NOT
  covered) -- 1 cell each with raw ``<font size="3"><strong>...
  </strong></font><br />``.

R67/B4 fixed ``CSOne_Detail_All`` but missed the other Snowflake
rich-text sheets, AND the Compact / Renewal / Leader inline writers
in ``app_simple.py`` weren't routing through the central
``write_excel_workbook`` allow-list at all.

The Round 70 fix has two parts:

1. Extends the ``_R66_HTML_STRIP_SHEETS`` allow-list in
   ``adoptiq_backend.py`` (used by ``write_excel_workbook`` ->
   Comprehensive) to cover the missing 4 sheets.
2. Adds source-side ``strip_html_from_dataframe`` calls inside the
   Compact / Renewal / Leader inline writers in ``app_simple.py`` so
   the same defense applies regardless of which writer path the report
   takes.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read_backend() -> str:
    return (PROJECT_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")


def _read_app_simple() -> str:
    return (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Allow-list contract (Comprehensive path via write_excel_workbook)
# ---------------------------------------------------------------------------


def test_r66_allow_list_covers_all_support_cases() -> None:
    src = _read_backend()
    assert '"All_Support_Cases"' in src, (
        "Round 70 / #10: All_Support_Cases MUST be on the HTML strip "
        "allow-list."
    )


def test_r66_allow_list_covers_customer_support_cases() -> None:
    src = _read_backend()
    assert '"Customer_Support_Cases"' in src, (
        "Round 70 / #10: Customer_Support_Cases MUST be on the HTML "
        "strip allow-list."
    )


def test_r66_allow_list_covers_external_incidents() -> None:
    src = _read_backend()
    assert '"External_Incidents"' in src, (
        "Round 70 / #10: External_Incidents MUST be on the HTML strip "
        "allow-list."
    )


def test_r66_allow_list_covers_tac_cases() -> None:
    src = _read_backend()
    assert '"TAC_Cases"' in src, (
        "Round 70 / #10: TAC_Cases MUST be on the HTML strip "
        "allow-list."
    )


# ---------------------------------------------------------------------------
# Source-side coverage (Compact / Renewal / Leader inline writers)
# ---------------------------------------------------------------------------


def test_compact_inline_writer_html_strip_present() -> None:
    """R70/Phase 3 (#10): the Compact inline writer in ``app_simple.py``
    MUST apply ``strip_html_from_dataframe`` to the rich-text sheets
    BEFORE iterating ``sheets.items()`` for write."""
    src = _read_app_simple()
    assert "_r70_compact_html_sheets" in src, (
        "Round 70 / #10: Compact writer MUST define an HTML strip sheet "
        "list."
    )
    assert '"All_Support_Cases"' in src, (
        "Round 70 / #10: Compact HTML strip MUST cover All_Support_Cases."
    )


def test_renewal_inline_writer_html_strip_present() -> None:
    """R70/Phase 3 (#10): the Renewal inline writer MUST apply HTML
    stripping to ``Customer_Support_Cases`` BEFORE write."""
    src = _read_app_simple()
    assert "_r70_renewal_html_sheets" in src, (
        "Round 70 / #10: Renewal writer MUST define an HTML strip sheet "
        "list."
    )
    assert "Customer_Support_Cases" in src, (
        "Round 70 / #10: Renewal HTML strip MUST cover "
        "Customer_Support_Cases."
    )


def test_leader_inline_writer_html_strip_present() -> None:
    """R70/Phase 3 (#10): the Leader inline writer MUST apply HTML
    stripping to ``TAC_Cases`` and ``External_Incidents`` BEFORE
    write."""
    src = _read_app_simple()
    assert "_r70_leader_html_sheets" in src, (
        "Round 70 / #10: Leader writer MUST define an HTML strip sheet "
        "list."
    )


# ---------------------------------------------------------------------------
# Helper contract (idempotent + drops markup)
# ---------------------------------------------------------------------------


def test_strip_html_from_dataframe_helper_callable() -> None:
    """The shared helper MUST stay importable and callable; if it ever
    moves the inline writers will lose their HTML defense."""
    from data_normalization import strip_html_from_dataframe
    assert callable(strip_html_from_dataframe), (
        "Round 70 / #10: strip_html_from_dataframe MUST stay importable "
        "from data_normalization."
    )


def test_strip_html_from_dataframe_drops_markup() -> None:
    """Behavioral check: the helper MUST drop ``<font>`` / ``<strong>``
    / ``<br>`` markup from string columns."""
    from data_normalization import strip_html_from_dataframe

    raw = pd.DataFrame({
        "Title": ["<font size='3'><strong>Outage</strong></font><br />"],
        "Severity": ["P1"],
    })
    cleaned = strip_html_from_dataframe(raw)
    title = str(cleaned.iloc[0]["Title"])
    assert "<font" not in title.lower(), (
        f"Round 70 / #10: strip_html MUST drop <font> markup; saw {title!r}"
    )
    assert "<strong" not in title.lower(), (
        f"Round 70 / #10: strip_html MUST drop <strong> markup; saw {title!r}"
    )
    assert "<br" not in title.lower(), (
        f"Round 70 / #10: strip_html MUST drop <br> markup; saw {title!r}"
    )
    assert "Outage" in title, (
        f"Round 70 / #10: strip_html MUST preserve user-visible text; "
        f"saw {title!r}"
    )


def test_strip_html_from_dataframe_idempotent() -> None:
    """Running strip twice MUST produce the same output as running
    once -- defends against accidentally double-stripping (which would
    eat the user-visible whitespace)."""
    from data_normalization import strip_html_from_dataframe

    raw = pd.DataFrame({
        "Title": ["persona : Admin\nOrgType : Customer"],
    })
    once = strip_html_from_dataframe(raw)
    twice = strip_html_from_dataframe(once)
    assert str(once.iloc[0]["Title"]) == str(twice.iloc[0]["Title"]), (
        "Round 70 / #10: strip_html MUST be idempotent so it can be "
        "applied at multiple layers without corruption."
    )
