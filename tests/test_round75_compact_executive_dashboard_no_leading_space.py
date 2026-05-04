"""Round 75 / Phase 4 (B4) -- strip leading space from Compact Sheet_Title:Executive_Dashboard.

Build 47 acceptance audit caught Compact's
``Sheet_Title:Executive_Dashboard`` row in ``Report_Info`` rendering
as `' Executive Dashboard - All Managers Portfolio Analysis'` --
note the leading space.  Every other Sheet_Title row (in Compact AND
Renewal AND Leader AND Comprehensive) had no leading space, so this
was a one-off cosmetic typo introduced by an `f" {...}"` template
that was never fixed.

R75/B4 fix: drop the leading space from the f-string template at
``app_simple.py`` (the ``dashboard_sheet_name`` branch -- the
non-dashboard branch at L10887 already had the correct shape).

Round 75 / Phase 4 (B4).  Made-with: Cursor.
"""

from __future__ import annotations

import re
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]
_APP_SIMPLE = _REPO_ROOT / "app_simple.py"


def test_compact_dashboard_sheet_title_no_leading_space():
    """The Compact Executive_Dashboard ``Sheet_Title`` template MUST
    NOT carry a leading space.  The ``f" {dashboard_sheet_name...}"``
    typo is the exact shape the audit caught."""
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    # Locate the dashboard branch's title_text assignment.
    idx = src.find("Round 75 / B4")
    assert idx != -1, "Round 75 / B4 marker missing from app_simple.py"

    block = src[idx: idx + 2000]
    # The forbidden literal: f" {dashboard_sheet_name.replace(...)} - "
    # (note the leading space inside the f-string).
    forbidden = re.search(
        r"title_text\s*=\s*f\"\s+\{dashboard_sheet_name\.replace",
        block,
    )
    assert forbidden is None, (
        "R75/B4 regression: Compact Executive_Dashboard Sheet_Title "
        "template carries a leading space again.  Audit caught it as "
        "' Executive Dashboard - All Managers Portfolio Analysis' "
        "(leading space).  Drop the space from the f-string."
    )

    # The fixed shape MUST be present.
    fixed = re.search(
        r"title_text\s*=\s*f\"\{dashboard_sheet_name\.replace\('_',\s*' '\)\}\s*-\s*\{manager\}\s+Portfolio Analysis\"",
        block,
    )
    assert fixed is not None, (
        "R75/B4: expected fixed template "
        "`f\"{dashboard_sheet_name.replace('_', ' ')} - {manager} Portfolio Analysis\"` "
        "in the dashboard branch but did not find it.  If you are "
        "renaming the variable, update the test."
    )


def test_compact_non_dashboard_sheet_title_remains_unchanged():
    """The non-dashboard branch at ~L10887 already had the correct
    shape.  Preserve it as a parity reference -- both branches now
    use the same leading-space-free template."""
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    expected = re.search(
        r"title_text\s*=\s*f\"\{sheet_name\.replace\('_',\s*' '\)\}\s*-\s*\{manager\}\s+Portfolio Analysis\"",
        src,
    )
    assert expected is not None, (
        "R75/B4 reference: non-dashboard Sheet_Title template (the "
        "always-correct branch around L10887) is missing or has "
        "drifted.  If you are renaming the variable, update both "
        "branches consistently."
    )


def test_no_other_leading_space_sheet_title_typos_in_app_simple():
    """Repo-wide guard: NO Sheet_Title template anywhere in app_simple
    may carry a leading space inside the f-string.  Catches
    a future copy-paste of the original buggy template."""
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    # Any title_text = f" <something>{... .replace('_', ' ')...} pattern
    # is suspicious.  The leading space must be outside the f-string
    # if the writer wants whitespace -- but for Sheet_Title rows we
    # never want leading whitespace.
    forbidden_patterns = [
        re.compile(r"title_text\s*=\s*f\"\s+\{[^}]+\.replace\("),
    ]
    for pattern in forbidden_patterns:
        matches = pattern.findall(src)
        assert not matches, (
            f"R75/B4 repo-wide regression: leading-space Sheet_Title "
            f"template re-appeared in app_simple.py: {matches}"
        )
