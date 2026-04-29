"""Round 48 / F-RP-MD-LEAK regression tests.

Pin that the renewal Word renderer strips markdown bold/italic
chrome from customer-name display strings before they reach the
docx layer.

Audit baseline (run 1777445582 -- renewal_doc.txt line 794):
    "ATLANTIA SPA__AEROPORTI DI ROMA SPA__IT - Rating: Green ..."

The source data uses double-underscore as a separator between
multi-account aliases (Atlantia SpA owns Aeroporti di Roma SpA in
Italy).  When a CommonMark / GFM-aware reader (Word reader plugin,
Outlook web preview, GitHub raw .docx renderer) renders the .docx,
the ``__AEROPORTI DI ROMA SPA__`` segment is interpreted as bold
italic and the underscores vanish into formatting chrome.

These tests assert the dedicated ``_strip_markdown_chrome`` helper
both exists and is wired into the customer-name rendering paths.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import app_simple


_APP_SIMPLE_PATH = Path(app_simple.__file__).resolve()


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


def test_round48_strip_markdown_chrome_is_callable():
    """The Round 48 helper must be a real callable on app_simple."""

    assert callable(app_simple._strip_markdown_chrome), (
        "app_simple._strip_markdown_chrome missing -- fix anchor "
        "F-RP-MD-LEAK is not in place"
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        # The audit baseline reproducer.
        (
            "ATLANTIA SPA__AEROPORTI DI ROMA SPA__IT",
            "ATLANTIA SPA AEROPORTI DI ROMA SPA IT",
        ),
        # Bold (** **).
        ("**Cisco Systems**", "Cisco Systems"),
        # Italic via underscore (_text_).
        ("_Important Customer_", "Important Customer"),
        # Italic via asterisks.
        ("*Customer Note*", "Customer Note"),
        # Inline code.
        ("`Important`", "Important"),
        # Strikethrough.
        ("~~Deprecated~~", "Deprecated"),
        # Markdown link.
        ("[Cisco](https://cisco.com)", "Cisco"),
        # Mixed combination.
        ("**ACME** _Inc._", "ACME Inc."),
        # No chrome -- pass through.
        ("Plain Customer Name", "Plain Customer Name"),
        # Empty / None safety.
        ("", ""),
    ],
)
def test_round48_strip_markdown_chrome_canonical_cases(raw, expected):
    actual = app_simple._strip_markdown_chrome(raw)
    # Normalize internal whitespace because __X__ -> X drops the
    # underscores but leaves a single boundary -- some inputs end
    # up with a single space rather than touching characters.
    actual_normalized = re.sub(r"\s+", " ", actual).strip()
    expected_normalized = re.sub(r"\s+", " ", expected).strip()
    assert actual_normalized == expected_normalized, (
        f"Strip markdown chrome failed: input={raw!r}; "
        f"expected={expected_normalized!r}; actual={actual_normalized!r}"
    )


def test_round48_strip_markdown_chrome_handles_none_and_non_string():
    """Robustness: None / int / object inputs must not crash."""

    assert app_simple._strip_markdown_chrome(None) == ""
    # Non-string inputs are coerced via str().
    assert "42" in app_simple._strip_markdown_chrome(42)


def test_round48_strip_markdown_chrome_does_not_mangle_legitimate_chars():
    """A valid customer name with single underscores between
    business words (e.g. ``MY_LLC``) must not be silently
    italicized away.  The italic regex is anchored on word
    boundaries so this case is preserved.
    """

    assert (
        app_simple._strip_markdown_chrome("MY_LLC enterprise") == "MY_LLC enterprise"
    )


# ---------------------------------------------------------------------------
# Wiring tests -- the renewal customer-name renderers must call the helper
# ---------------------------------------------------------------------------


def test_round48_app_simple_has_fix_anchor():
    src = _APP_SIMPLE_PATH.read_text(encoding="utf-8")
    assert "F-RP-MD-LEAK" in src, (
        "Round 48 fix anchor F-RP-MD-LEAK missing from app_simple.py"
    )


def test_round48_renewal_pulse_cust_label_uses_strip_markdown_chrome():
    """The five ``cust_label = f'Customer: {cn} - '`` sites in
    app_simple.py (renewal Word renderer) must wrap ``cn`` in
    ``_strip_markdown_chrome`` so that customer-pulse, success-
    priorities, action-plans, AB-narrative, and TAC-narrative all
    consistently strip __italic__ chrome.
    """

    src = _APP_SIMPLE_PATH.read_text(encoding="utf-8")
    # Legacy un-stripped pattern must be gone.
    legacy = "cust_label = f'Customer: {cn} — '"
    assert legacy not in src, (
        "Renewal renderer still has un-stripped Customer: {cn} sites; "
        "F-RP-MD-LEAK fix incomplete"
    )
    # New pattern must appear at least 5 times (one per render site).
    new_pattern = "_strip_markdown_chrome(cn)"
    occurrences = src.count(new_pattern)
    assert occurrences >= 5, (
        f"Expected >=5 _strip_markdown_chrome(cn) sites in renewal "
        f"renderer; found {occurrences}"
    )


def test_round48_subscription_title_strips_markdown():
    """The Subscription Analysis title heading must call
    ``_strip_markdown_chrome`` on the customer name so the title
    page does not display ``__ALIAS__`` italics.
    """

    src = _APP_SIMPLE_PATH.read_text(encoding="utf-8")
    assert (
        "_strip_markdown_chrome(sub_data.get(\"customer_name\")"
        in src
    ), "Subscription Analysis title heading not wired through _strip_markdown_chrome"
