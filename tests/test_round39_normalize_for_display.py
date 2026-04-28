"""Round 39 / Phase 4.4 — ``normalize_for_display`` collapses ``__``.

Pre-Round-39 the leader report's per-account heading rendered raw
``BU_ACCOUNT_NAME`` strings like
``"TRIBUNAL...__GOBIERNO...__MX"`` -- a reader couldn't tell whether
the underscores were significant or noise.

Round 39 / Phase 4.4 introduces ``normalize_for_display`` which:

  * Inherits NFKC + whitespace collapse from ``normalize_customer_name``.
  * Replaces runs of >= 2 underscores with ", " so segments read as
    a comma-separated list a human can parse.
  * Preserves single underscores (which appear in real customer
    names like ``"Acme_Subsidiary"``).
"""
from __future__ import annotations

import pytest

from data_normalization import normalize_for_display


@pytest.mark.parametrize("raw,expected", [
    ("TRIBUNAL DE JUSTICIA__GOBIERNO__MX",
     "TRIBUNAL DE JUSTICIA, GOBIERNO, MX"),
    ("ABC___DEF", "ABC, DEF"),
    ("Acme_Subsidiary", "Acme_Subsidiary"),  # single underscore preserved
    ("  Acme  Corp  ", "Acme Corp"),
    ("__Leading", "Leading"),
    ("Trailing__", "Trailing"),
    ("", "Unknown"),
    (None, "Unknown"),
])
def test_normalize_for_display(raw, expected):
    assert normalize_for_display(raw) == expected


def test_leader_report_uses_normalize_for_display_in_account_heading():
    """Pin that ``_add_customer_summary_with_sources`` routes the
    customer name through ``normalize_for_display`` before rendering
    the Account heading."""
    import inspect
    import leader_report_generator as lrg
    src = inspect.getsource(
        lrg.LeaderReportGenerator._add_customer_summary_with_sources
    )
    assert "normalize_for_display" in src, (
        "Round 39 / Phase 4.4: _add_customer_summary_with_sources "
        "must route customer through ``normalize_for_display`` before "
        "rendering the heading -- otherwise raw ``__`` separators "
        "leak into customer-facing text."
    )
