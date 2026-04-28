"""Round 32 / Phase 1.C regression: the CSConsole technology filter
must accept legitimate tech text that the strict per-bucket regexes
would otherwise drop.

Build6 dropped 2 of 2 surviving CSConsole rows for a real
Contact-Center customer because none of the rows said the literal
"All Contact Center" or matched any of the bucket-specific regex
patterns.  See ``~/.adoptiq/adoptiq.46198.log`` line 230.
"""
from __future__ import annotations

import pytest


def test_all_contact_center_matches_canonical_text() -> None:
    from adoptiq_backend import _filter_tech_text_enhanced
    assert _filter_tech_text_enhanced(
        "Webex Contact Center", "", "All Contact Center"
    ) is True


def test_all_contact_center_fuzzy_matches_plain_contact_center() -> None:
    from adoptiq_backend import _filter_tech_text_enhanced
    # Plain "Contact Center" (no qualifier) is rejected by every
    # strict per-bucket regex; the fuzzy fallback is the only thing
    # that keeps these rows in the report.
    assert _filter_tech_text_enhanced(
        "Contact Center Inquiry", "", "All Contact Center"
    ) is True
    assert _filter_tech_text_enhanced(
        "Plain Contact Center", "", "All Contact Center"
    ) is True


def test_all_contact_center_fuzzy_uses_sub_tech_field_too() -> None:
    from adoptiq_backend import _filter_tech_text_enhanced
    assert _filter_tech_text_enhanced(
        "", "Contact Center Inquiry", "All Contact Center"
    ) is True


def test_specific_bucket_does_not_borrow_fuzzy_fallback() -> None:
    """Only ``All <X>`` filters go through the fuzzy path; specific
    buckets must continue to reject mismatched text or the per-tech
    aggregations would silently merge buckets."""
    from adoptiq_backend import _filter_tech_text_enhanced
    assert _filter_tech_text_enhanced(
        "Generic Contact Center", "", "Webex Calling"
    ) is False


def test_null_or_empty_tech_columns_do_not_crash() -> None:
    from adoptiq_backend import _filter_tech_text_enhanced
    assert _filter_tech_text_enhanced(None, None, "All Contact Center") is False
    assert _filter_tech_text_enhanced("", "", "All Contact Center") is False


def test_unknown_all_bucket_falls_back_to_suffix_substring() -> None:
    """Generic ``All <X>`` for a bucket not pre-registered in the
    synonym map still does a permissive suffix substring check so the
    matcher degrades gracefully if a new top-level tech ships before
    the synonym map is updated."""
    from adoptiq_backend import _filter_tech_text_fuzzy_all_bucket
    assert _filter_tech_text_fuzzy_all_bucket(
        "Cisco DNA Center", "", "All DNA Center"
    ) is True
    assert _filter_tech_text_fuzzy_all_bucket(
        "Webex Calling", "", "All DNA Center"
    ) is False


def test_fuzzy_helper_rejects_non_all_buckets() -> None:
    from adoptiq_backend import _filter_tech_text_fuzzy_all_bucket
    assert _filter_tech_text_fuzzy_all_bucket(
        "Cisco DNA Center", "", "Webex Calling"
    ) is False


def test_fuzzy_match_logs_breadcrumb_for_diagnostics(caplog) -> None:
    """Round 32 / Phase 1.C: every fuzzy match must INFO-log so the
    next thin-report incident has a clear breadcrumb that the strict
    matcher would have dropped the row.  The log noise is bounded by
    how many rows survive the upstream account/customer filter."""
    import logging
    from adoptiq_backend import _filter_tech_text_enhanced
    backend_logger = logging.getLogger("adoptiq_backend")
    prior_propagate = backend_logger.propagate
    backend_logger.propagate = True
    try:
        with caplog.at_level(logging.INFO, logger="adoptiq_backend"):
            _filter_tech_text_enhanced(
                "Contact Center Inquiry", "", "All Contact Center"
            )
        assert any(
            "Round 32 / Phase 1.C" in rec.getMessage() for rec in caplog.records
        ), [rec.getMessage() for rec in caplog.records]
    finally:
        backend_logger.propagate = prior_propagate
