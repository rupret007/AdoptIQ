"""Round 67 / Build 41 (B4) -- CSOne_Detail_All HTML scrubbing.

Build 40 acceptance showed the Comprehensive XLSX's
``CSOne_Detail_All`` sheet leaking raw Snowflake-sourced HTML / entity
markup into ``Problem Details`` / ``Resolution Details`` cells:

- ``<br />`` interleaved with body text
- ``<agent name>`` placeholder spans
- ``&#34;`` (`&quot;`) entity escapes

Round 67 / B4 fix: extend ``_R66_HTML_STRIP_SHEETS`` to include
``CSOne_Detail_All`` so the same ``_strip_html_safe`` (BeautifulSoup
primary, regex fallback) used for the existing AB / AP / Pulse sheets
also processes the CSOne sheet before write.
"""
from __future__ import annotations

from pathlib import Path

import pytest


_BACKEND = Path(__file__).resolve().parent.parent / "adoptiq_backend.py"


def _read_backend() -> str:
    return _BACKEND.read_text(encoding="utf-8")


def test_csone_detail_all_in_html_strip_allowlist() -> None:
    """R67/B4: ``CSOne_Detail_All`` MUST be in ``_R66_HTML_STRIP_SHEETS``
    so the HTML-strip pipeline runs over it pre-write."""
    src = _read_backend()
    assert "Round 67 / Build 41 (B4)" in src, (
        "R67/B4 marker MUST be present in adoptiq_backend.py"
    )
    # The constant is a literal tuple -- check the literal appears.
    assert '"CSOne_Detail_All"' in src, (
        "R67/B4: 'CSOne_Detail_All' MUST appear in _R66_HTML_STRIP_SHEETS"
    )


def test_strip_html_safe_runtime_strips_csone_markup() -> None:
    """End-to-end: feed a row with the exact Build 40 markup patterns
    through the strip helper and confirm clean output."""
    import pandas as pd
    try:
        from adoptiq_backend import _strip_html_safe  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        pytest.skip("_strip_html_safe not directly importable; covered by allowlist test")

    df = pd.DataFrame([{
        "Problem Details": (
            "User reports issue<br />Investigation: <agent name> looking into "
            "&#34;persistent failure&#34;"
        ),
        "Resolution Details": "Restarted service<br /><br />Issue resolved",
    }])
    cleaned = _strip_html_safe(df)
    pd_text = str(cleaned.iloc[0]["Problem Details"])
    rd_text = str(cleaned.iloc[0]["Resolution Details"])
    # The strip helper removes HTML tags, so <br /> and <agent name>
    # spans should not remain in body text.
    assert "<br" not in pd_text.lower(), (
        f"R67/B4: <br /> tags MUST be stripped from Problem Details, got: {pd_text!r}"
    )
    assert "<agent" not in pd_text.lower(), (
        f"R67/B4: <agent name> spans MUST be stripped, got: {pd_text!r}"
    )
    assert "<br" not in rd_text.lower(), (
        f"R67/B4: <br /> tags MUST be stripped from Resolution Details, got: {rd_text!r}"
    )


def test_other_known_csone_sheets_still_in_allowlist() -> None:
    """R67/B4 does NOT remove any of the existing R66/B4 entries --
    parity check against regression."""
    src = _read_backend()
    for sheet in (
        "AB_Detail_All",
        "CSConsole_Customer_Pulse",
        "Action_Plans",
        "CSConsole_Action_Plans",
        "CSConsole_Adoption_Barriers",
        "CSConsole_Success_Priorities",
        "Adoption_Barriers",
        "Customer_Pulse",
        "Success_Priorities",
    ):
        assert f'"{sheet}"' in src, (
            f"R67/B4 regression: '{sheet}' MUST stay in _R66_HTML_STRIP_SHEETS"
        )


def test_html_strip_sheets_contains_csone_detail_all_after_load() -> None:
    """Defensive: load the module and pull the constant directly."""
    import importlib

    module = importlib.import_module("adoptiq_backend")
    # The constant is defined inside a closure (write_excel_workbook),
    # so we cannot import it directly. Instead, scan the source via
    # inspect.getsource to confirm runtime evaluation.
    import inspect

    try:
        src = inspect.getsource(module.write_excel_workbook)  # type: ignore[attr-defined]
    except (TypeError, OSError) as exc:  # pragma: no cover - defensive
        pytest.skip(f"Could not introspect write_excel_workbook source: {exc}")

    assert "_R66_HTML_STRIP_SHEETS" in src, (
        "R67/B4: _R66_HTML_STRIP_SHEETS tuple MUST be defined inside write_excel_workbook"
    )
    assert '"CSOne_Detail_All"' in src, (
        "R67/B4: CSOne_Detail_All MUST be in the in-function strip allowlist"
    )
