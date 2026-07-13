"""Round 45 / Phase 8 + 9 regression: ``apply_export_schema`` MUST
scrub residual Markdown chrome (``**bold**``, ``__italic__``) and
literal None / NaN cells from the named free-form text columns at the
Excel write site.

The 2026-04-28 Build-20 audit found:

* Renewal ``Customer_Action_Plans`` row 148 / col 19 (Comments):
  ``**Classic Calabrio***delete old report``
* Renewal ``Customer_Adoption_Barriers`` rows 165 (Description / Comments):
  bold markdown intact
* Leader ``Action_Plans`` row 148: same ``**`` chrome
* 6 cells in Renewal ``Customer_Action_Plans`` and 2 in Leader
  ``Action_Plans``: literal ``None`` strings

Round 44 / Phase 7 only stripped TAC subjects in Word.  Phase 8 + 9
extend the SSoT cleanup to the Excel write site via
``_r45_clean_excel_body_cell`` and ``_r45_clean_body_columns``,
applied to ``_BODY_TEXT_COLUMNS_FRIENDLY`` AFTER the Phase 5 friendly
rename.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


from report_export_schema import (  # noqa: E402
    _r45_clean_excel_body_cell,
    apply_export_schema,
)


def test_cell_scrubber_strips_bold_markdown() -> None:
    """``**bold**`` runs MUST collapse to the inner text.  Pins the
    primary regex from the Build-20 audit: ``**Classic Calabrio***``."""
    assert _r45_clean_excel_body_cell("**Classic Calabrio***delete old report") == (
        "Classic Calabriodelete old report"
    )


def test_cell_scrubber_strips_underscore_italics() -> None:
    """``__italic__`` runs MUST be removed at word boundary -- but
    embedded snake_case identifiers (``snake_case_col``) survive."""
    out = _r45_clean_excel_body_cell("Hello __strong__ world")
    assert "__" not in out
    assert "world" in out
    assert _r45_clean_excel_body_cell("snake_case_col") == "snake_case_col"


def test_cell_scrubber_coerces_none_and_nan() -> None:
    """All flavors of None / NaN / NA MUST coerce to empty string."""
    assert _r45_clean_excel_body_cell(None) == ""
    assert _r45_clean_excel_body_cell("None") == ""
    assert _r45_clean_excel_body_cell("nan") == ""
    assert _r45_clean_excel_body_cell("NaN") == ""
    assert _r45_clean_excel_body_cell("<NA>") == ""
    assert _r45_clean_excel_body_cell(np.nan) == ""
    assert _r45_clean_excel_body_cell(pd.NA) == ""


def test_cell_scrubber_preserves_plain_text() -> None:
    """Regular sentences MUST NOT be touched.  Single asterisks (used
    as legitimate punctuation, e.g. footnote markers) survive."""
    assert _r45_clean_excel_body_cell("Regular comment text.") == (
        "Regular comment text."
    )
    assert _r45_clean_excel_body_cell("Note: see *footnote 1") == (
        "Note: see *footnote 1"
    )


def test_cell_scrubber_collapses_whitespace() -> None:
    """Whitespace introduced by stripping (e.g. between former emphasis
    markers) MUST collapse to a single space."""
    assert _r45_clean_excel_body_cell("a  ** **  b") == "a b"


def test_apply_export_schema_cleans_comments_column() -> None:
    """End-to-end: a ``COMMENTS_C`` column with markdown + None values
    MUST emerge from ``apply_export_schema`` as a clean ``Comments``
    column with the chrome stripped and None coerced to empty."""
    df = pd.DataFrame(
        {
            "BU_NAME": ["A", "B", "C"],
            "COMMENTS_C": [
                "**Classic Calabrio***delete old report",
                None,
                "regular comment",
            ],
        }
    )
    out = apply_export_schema(df, sheet_name="Customer_Action_Plans")
    assert "Comments" in out.columns
    vals = list(out["Comments"])
    assert vals[0] == "Classic Calabriodelete old report"
    assert vals[1] == ""
    assert vals[2] == "regular comment"


def test_apply_export_schema_does_not_touch_non_body_columns() -> None:
    """The cleaner is conservative: it MUST NOT touch columns outside
    the body-text SSoT (so categorical columns where ``"None"`` is a
    real value -- e.g. ``hold_reason="None"`` meaning "not on hold" --
    are preserved)."""
    df = pd.DataFrame(
        {
            "BU_NAME": ["A"],
            "AB_HOLD_REASON_C": ["None"],  # categorical 'None' is real
        }
    )
    out = apply_export_schema(df, sheet_name="Adoption_Barriers")
    # Hold Reason is the friendly label for AB_HOLD_REASON_C.
    assert "Hold Reason" in out.columns
    # Categorical 'None' string is NOT a body-text column so it must
    # survive unchanged.  This is the conservative-cleanup contract:
    # we'd rather leak a few literal "None" cells than mangle a
    # legitimate categorical value.
    assert list(out["Hold Reason"]) == ["None"]
