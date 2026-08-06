"""Round 130 — B1 scope-banner SSoT (Round 126) regression pins.

``data_normalization.partial_data_warnings_all_scope`` and
``partial_data_banner_preamble`` are the single source of truth for
scope-vs-load partial-data banner wording.  Every report-format banner
site must route through these helpers (or the thin ``_r126_*`` aliases)
so the kind set cannot drift again.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

import os

import pytest

from data_normalization import (
    PARTIAL_DATA_SCOPE_EXCLUSION_KINDS,
    partial_data_banner_preamble,
    partial_data_warnings_all_scope,
)


@pytest.mark.parametrize(
    "kind",
    sorted(PARTIAL_DATA_SCOPE_EXCLUSION_KINDS),
)
def test_each_scope_kind_is_all_scope_when_sole_warning(kind: str) -> None:
    assert partial_data_warnings_all_scope([{"kind": kind, "dataset": "ab", "error": "x"}])


def test_tech_filter_scope_prefix_counts_as_scope() -> None:
    assert partial_data_warnings_all_scope([
        {"kind": "tech_filter_scope_excluded_custom", "dataset": "ab", "error": "x"},
    ])


def test_mixed_scope_and_load_is_not_all_scope() -> None:
    warnings = [
        {"kind": "tech_filter_scope_excluded", "dataset": "ab", "error": "scoped"},
        {"kind": "schema_drift", "dataset": "support_cases", "error": "missing col"},
    ]
    assert not partial_data_warnings_all_scope(warnings)


def test_empty_warnings_is_not_all_scope() -> None:
    assert not partial_data_warnings_all_scope([])
    assert not partial_data_warnings_all_scope(None)


def test_scope_preamble_mentions_filtered_not_failed() -> None:
    text = partial_data_banner_preamble([
        {"kind": "tech_filter_empty_after_scope", "dataset": "ab", "error": "0 rows"},
    ])
    assert_in_source(text, "filtered out by the requested scope", label='text')
    assert "failed to load" not in text


def test_load_preamble_mentions_failed_to_load() -> None:
    text = partial_data_banner_preamble([
        {"kind": "schema_drift", "dataset": "support_cases", "error": "missing"},
    ])
    assert_in_source(text, "failed to load", label='text')


def test_excel_mention_flag_appends_workbook_sentence() -> None:
    text = partial_data_banner_preamble(
        [{"kind": "tech_filter_scope_excluded", "dataset": "ab", "error": "x"}],
        mention_excel=True,
    )
    assert_in_source(text, "Excel workbook", label='text')


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize(
    "rel_path,needle",
    [
        ("app_simple.py", "_r126_partial_data_all_scope"),
        ("executive_report_builder.py", "partial_data_warnings_all_scope"),
        ("leader_report_generator.py", "partial_data_banner_preamble"),
        ("compact_report_formatter.py", "partial_data_banner_preamble"),
        ("executive_intelligence_formatter.py", "partial_data_banner_preamble"),
        ("data_normalization.py", "PARTIAL_DATA_SCOPE_EXCLUSION_KINDS"),
    ],
)
def test_banner_sites_reference_ssot(rel_path: str, needle: str) -> None:
    path = os.path.join(_repo_root(), rel_path)
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    assert needle in src, f"{rel_path} must reference {needle}"


def test_app_simple_has_two_banner_classifier_call_sites() -> None:
    path = os.path.join(_repo_root(), "app_simple.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    assert src.count("_r126_partial_data_all_scope(") >= 2


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
