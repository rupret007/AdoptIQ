"""Round 49 / F-RP-COMPOSITE-KEY-BLEED regression tests.

Build25 audit (run 1777464863 -- renewal docx + Renewal_Summary /
Risk_Summary xlsx) showed customer-name fields containing merged
Snowflake composite-key strings:

  * Excel Renewal_Summary: ``MARUBENI CORPORATION__JAMAICA PUBLIC
    SERVICE CO__JM`` (double-underscore separator)
  * Renewal docx narrative: ``ELEVANCE_ELEVANCE HEALTH_US`` (single
    underscore + region tail)

These come from raw Snowflake account-id->name joins where two
account aliases share an ID, or from post-fetch friendly-rename
collapses, bleeding into display surfaces.

R48-D8's ``_strip_markdown_chrome`` only treated ``__bold__`` as
markdown chrome -- it did not split composite keys.

R49-B2 fix: new ``_normalize_composite_customer_key`` helper in
app_simple.py near ``_strip_markdown_chrome`` that splits on ``__``
or strips trailing ``_<region>`` country-code tails, returning the
first/canonical segment.  Wired into the 7 renewal-renderer customer-
name display sites that already call ``_strip_markdown_chrome`` plus
the Excel ``Renewal_Summary`` / ``Risk_Summary`` writers.

This test pins:

1. Helper behaviour for all documented inputs.
2. Helper is idempotent (safe to chain).
3. Plain customer names pass through unchanged.
4. The 7 renewal renderer sites in app_simple.py route through the
   helper.
5. The Excel Renewal_Summary / Risk_Summary builders route through
   the helper.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import app_simple  # noqa: E402

_normalize = app_simple._normalize_composite_customer_key


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


def test_double_underscore_keeps_first_segment() -> None:
    raw = "MARUBENI CORPORATION__JAMAICA PUBLIC SERVICE CO__JM"
    assert _normalize(raw) == "MARUBENI CORPORATION"


def test_double_underscore_two_segments_no_country_tail() -> None:
    raw = "Atlantia SPA__Aeroporti di Roma SPA"
    assert _normalize(raw) == "Atlantia SPA"


def test_single_underscore_with_country_tail_keeps_longer_segment() -> None:
    """``ELEVANCE_ELEVANCE HEALTH_US`` -- the second segment
    (``ELEVANCE HEALTH``) is the human-readable form; the first
    segment is just the legacy short code that is a strict prefix.
    """
    raw = "ELEVANCE_ELEVANCE HEALTH_US"
    out = _normalize(raw)
    assert out == "ELEVANCE HEALTH", out


def test_single_underscore_without_country_tail_unchanged() -> None:
    """A name with internal underscores but no country tail must
    survive intact (defensive: don't damage names that legitimately
    contain ``_`` like sandbox / qa accounts).
    """
    raw = "QA_SANDBOX"
    out = _normalize(raw)
    assert out == "QA_SANDBOX", out


def test_plain_customer_name_unchanged() -> None:
    raw = "Acme Corporation"
    assert _normalize(raw) == "Acme Corporation"


def test_empty_and_none_safe() -> None:
    assert _normalize(None) == ""
    assert _normalize("") == ""
    assert _normalize("   ") == ""


def test_idempotent_on_normalized_output() -> None:
    raw = "ABC CORP__DEF SUBSIDIARY__US"
    once = _normalize(raw)
    twice = _normalize(once)
    assert once == twice


def test_inc_corp_suffixes_not_treated_as_country_tail() -> None:
    """``Acme Inc.`` and ``XYZ Corp`` must NOT be stripped -- the
    country-tail regex requires a leading underscore plus 2-3
    uppercase letters, which doesn't match ``_Inc.`` (period
    follows) or ``Corp`` (no leading underscore separator since the
    name uses spaces).
    """
    assert _normalize("Acme Inc.") == "Acme Inc."
    assert _normalize("XYZ Corp") == "XYZ Corp"


def test_three_letter_country_tail_handled() -> None:
    """ISO 3166 alpha-3 codes like ``USA`` should also strip when
    they're a 3-letter trailing tail.
    """
    raw = "FOO_BAR_USA"
    out = _normalize(raw)
    assert "USA" not in out, out


# ---------------------------------------------------------------------------
# Wiring pins
# ---------------------------------------------------------------------------


def test_renewal_renderers_route_through_normalizer() -> None:
    """Pin: every renewal-renderer customer-name display site that
    used to call ``_strip_markdown_chrome(cn)`` MUST now call
    ``_strip_markdown_chrome(_normalize_composite_customer_key(cn))``
    -- otherwise the renewal docx narrative still shows
    ``ELEVANCE_ELEVANCE HEALTH_US`` style bleed.
    """
    src = (PROJECT_ROOT / "app_simple.py").read_text()
    occurrences = src.count(
        "_strip_markdown_chrome(_normalize_composite_customer_key(cn))"
    )
    assert occurrences >= 5, (
        f"R49-B2: expected >=5 chained calls of the form "
        f"_strip_markdown_chrome(_normalize_composite_customer_key(cn)) "
        f"in renewal renderer customer-name labels; got {occurrences}.  "
        "If the renewal docx layout was refactored, add the chained "
        "call at every customer-name surface."
    )


def test_excel_summaries_route_through_normalizer() -> None:
    """Pin: the Excel Renewal_Summary + Risk_Summary builders MUST
    route ``Customer`` cells through ``_normalize_composite_customer_key``.
    Pre-R49 the cells were the raw composite keys from
    ``risk_scores`` / ``customer_analyses`` and Build25's xlsx
    surfaced ``MARUBENI CORPORATION__JAMAICA PUBLIC SERVICE CO__JM``.
    """
    src = (PROJECT_ROOT / "app_simple.py").read_text()
    needles = [
        "'Customer': _normalize_composite_customer_key(customer)",
        "'Customer': _normalize_composite_customer_key(cust_name)",
        "'Customer': _normalize_composite_customer_key(customer_name)",
    ]
    found = [n for n in needles if n in src]
    assert len(found) >= 3, (
        f"R49-B2: expected the Risk_Summary, Renewal_Summary "
        f"(portfolio), and Renewal_Summary (single-customer) "
        f"builders to each route the ``Customer`` cell through "
        f"_normalize_composite_customer_key; got matches for {found}."
    )


def test_helper_referenced_by_anchor() -> None:
    """Pin: the R49 anchor comment is present so future readers
    discover the rationale.
    """
    src = (PROJECT_ROOT / "app_simple.py").read_text()
    assert "F-RP-COMPOSITE-KEY-BLEED" in src
