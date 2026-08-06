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
from source_shape_utils import assert_in_source, assert_any_in_source, assert_not_in_source, count_in_source

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
    assert_in_source(src, "F-RP-MD-LEAK", label='src')


def test_round48_renewal_pulse_cust_label_uses_strip_markdown_chrome():
    """The five ``cust_label = f'Customer: {cn} - '`` sites in
    app_simple.py (renewal Word renderer) must wrap ``cn`` in
    ``_strip_markdown_chrome`` so that customer-pulse, success-
    priorities, action-plans, AB-narrative, and TAC-narrative all
    consistently strip __italic__ chrome.

    Round 49 / F-RP-COMPOSITE-KEY-BLEED extended the wrap into a
    composition: ``_strip_markdown_chrome(_normalize_composite_customer_key(cn))``
    so the renderer also collapses raw Snowflake composite-key
    strings (``X__Y__US`` -> ``X``).  Both forms preserve the
    Round-48 invariant (markdown chrome is stripped); count
    occurrences across either form.
    """

    src = _APP_SIMPLE_PATH.read_text(encoding="utf-8")
    # Legacy un-stripped pattern must be gone.
    legacy = "cust_label = f'Customer: {cn} — '"
    assert_not_in_source(src, legacy, label='src')
    # The wrap may be the bare R48 form ``_strip_markdown_chrome(cn)``
    # OR the R49 composite-key chained form
    # ``_strip_markdown_chrome(_normalize_composite_customer_key(cn))``.
    # Count both -- the renderer must have at least 5 wrapped sites
    # in total.
    bare = count_in_source(src, "_strip_markdown_chrome(cn)")
    chained = src.count(
        "_strip_markdown_chrome(_normalize_composite_customer_key(cn))"
    )
    occurrences = bare + chained
    assert occurrences >= 5, (
        f"Expected >=5 _strip_markdown_chrome(cn) (bare) or "
        f"_strip_markdown_chrome(_normalize_composite_customer_key(cn)) "
        f"(chained) sites in renewal renderer; found bare={bare} "
        f"chained={chained}"
    )


def test_round48_subscription_title_strips_markdown():
    """The Subscription Analysis title heading must call
    ``_strip_markdown_chrome`` on the customer name so the title
    page does not display ``__ALIAS__`` italics.

    Round 49 extended the wrap to the composite-key normalizer
    chain; either bare or chained form is acceptable.
    """

    src = _APP_SIMPLE_PATH.read_text(encoding="utf-8")
    assert_any_in_source(
        src,
        "_strip_markdown_chrome(_normalize_composite_customer_key(sub_data.get",
        "_strip_markdown_chrome(sub_data.get",
        label="src",
    )
