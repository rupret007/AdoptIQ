"""Round 48 / F-COMP-AB-SUMMARY-VS-DETAIL-4 regression tests.

Pin the Excel ``Summary`` sheet's "Adoption barriers (detail rows)"
disclosure so that readers can reconcile the headline distinct-IDs
count (which uses ``cm.count_total_barriers`` and so dedupes by the
``ID`` column -- Round 25 / Phase F) against the underlying
``AB_Detail_All`` row count.  Pre-Round 48 the headline read "68" and
the detail tab read "72" with no in-workbook explanation; recipients
were left to guess that the four-row gap was multi-assignee fan-out.

These tests intentionally do NOT relabel the headline "(total)" row
because Round 15/16/19/21/23 golden-fixture tests pin that exact
string and the backwards-compatible additive disclosure approach is
documented in ``QUALITY_AUDIT.md`` against this ticket.
"""

from __future__ import annotations

import pandas as pd
import pytest

import report_export_styling as styling


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_ab(*, row_ids: list[str]) -> pd.DataFrame:
    """Return a minimal AB_Detail_All-shaped frame whose rows include
    multi-assignee duplicates (same ``ID`` appearing more than once).
    """

    return pd.DataFrame({"ID": row_ids, "ASSIGNEE": list("abcdefghijklmno"[: len(row_ids)])})


# ---------------------------------------------------------------------------
# F-COMP-AB-SUMMARY-VS-DETAIL-4 regressions
# ---------------------------------------------------------------------------


def test_round48_summary_includes_detail_rows_disclosure_when_duplicates_present():
    """The Summary sheet must include an "Adoption barriers (detail rows)"
    row that names the duplicate count when ``len(ab) > nunique(ID)``.

    The reference reproducer is the audit baseline (run 1777445582):
    8 rows, 4 distinct IDs -> "(detail rows): 8 (4 multi-assignee
    duplicates)".  Pin both the row label (so the disclosure cannot
    silently disappear) and the formatted string (so a refactor that
    swaps a comma for a slash etc is caught).
    """

    ab = _build_ab(
        row_ids=["AB-1", "AB-1", "AB-2", "AB-2", "AB-3", "AB-3", "AB-4", "AB-4"],
    )
    rows = styling.build_summary_rows(
        {"AB_Detail_All": ab, "CSOne_Detail_All": pd.DataFrame()},
        {"customer_pulse": pd.DataFrame()},
    )
    by_label = dict(rows)
    assert "Adoption barriers (detail rows)" in by_label, (
        "Round 48 disclosure row missing from Excel Summary sheet"
    )
    detail_label = by_label["Adoption barriers (detail rows)"]
    assert detail_label == "8 (4 multi-assignee duplicates)", (
        "Expected '8 (4 multi-assignee duplicates)' but got "
        f"{detail_label!r} -- the multi-assignee delta string changed"
    )


def test_round48_summary_singular_duplicate_uses_singular_noun():
    """One multi-assignee duplicate must read "duplicate" not "duplicates"."""

    ab = _build_ab(row_ids=["AB-1", "AB-1", "AB-2", "AB-3", "AB-4"])
    rows = styling.build_summary_rows(
        {"AB_Detail_All": ab, "CSOne_Detail_All": pd.DataFrame()},
        {"customer_pulse": pd.DataFrame()},
    )
    by_label = dict(rows)
    assert by_label.get("Adoption barriers (detail rows)") == (
        "5 (1 multi-assignee duplicate)"
    ), "Singular duplicate noun regressed"


def test_round48_summary_zero_duplicates_says_no_duplicates():
    """When every detail row has a unique ID the disclosure must
    affirmatively state "no multi-assignee duplicates" rather than
    leaving the row blank or repeating the headline number with no
    suffix.  This guards against a silent regression where a refactor
    drops the disclosure when there is nothing to disclose -- the row
    must always be present so its absence is itself a visible change.
    """

    ab = _build_ab(row_ids=["AB-1", "AB-2", "AB-3"])
    rows = styling.build_summary_rows(
        {"AB_Detail_All": ab, "CSOne_Detail_All": pd.DataFrame()},
        {"customer_pulse": pd.DataFrame()},
    )
    by_label = dict(rows)
    assert by_label.get("Adoption barriers (detail rows)") == (
        "3 (no multi-assignee duplicates)"
    )


def test_round48_summary_missing_ab_frame_emits_dashes_not_crash():
    """When the AB sheet is missing (e.g. a report that was generated
    with no Snowflake connectivity) the disclosure row must still be
    present and render "--" rather than crashing the Summary build.
    """

    rows = styling.build_summary_rows(
        {"CSOne_Detail_All": pd.DataFrame()},
        {"customer_pulse": pd.DataFrame()},
    )
    by_label = dict(rows)
    assert by_label.get("Adoption barriers (detail rows)") == "--"


def test_round48_summary_disclosure_row_appears_immediately_after_total():
    """Row order matters for the human reader: the disclosure must sit
    directly under "Adoption barriers (total)" so the eye lands on the
    explanation in the same glance.  Pin the relative order so a
    future cleanup pass does not separate them.
    """

    ab = _build_ab(row_ids=["AB-1", "AB-1", "AB-2"])
    rows = styling.build_summary_rows(
        {"AB_Detail_All": ab, "CSOne_Detail_All": pd.DataFrame()},
        {"customer_pulse": pd.DataFrame()},
    )
    labels = [r[0] for r in rows]
    assert "Adoption barriers (total)" in labels
    assert "Adoption barriers (detail rows)" in labels
    total_idx = labels.index("Adoption barriers (total)")
    detail_idx = labels.index("Adoption barriers (detail rows)")
    assert detail_idx == total_idx + 1, (
        f"Expected detail-rows row immediately after total row; "
        f"got total at {total_idx}, detail at {detail_idx}"
    )


# ---------------------------------------------------------------------------
# Backward-compatibility safety net
# ---------------------------------------------------------------------------


def test_round48_existing_total_label_unchanged():
    """Round 15 / 16 / 19 / 21 / 23 golden-fixture tests pin the exact
    label "Adoption barriers (total)".  R48 is purely additive; the
    canonical label must be byte-identical so the legacy assertions
    remain green.  This is a belt-and-braces test against an
    accidental relabel during R48 review.
    """

    ab = _build_ab(row_ids=["AB-1", "AB-2"])
    rows = styling.build_summary_rows(
        {"AB_Detail_All": ab, "CSOne_Detail_All": pd.DataFrame()},
        {"customer_pulse": pd.DataFrame()},
    )
    labels = [r[0] for r in rows]
    assert "Adoption barriers (total)" in labels


@pytest.mark.parametrize(
    "row_ids,expected_total",
    [
        (["AB-1"], "1"),
        (["AB-1", "AB-1"], "1"),
        (["AB-1", "AB-2", "AB-2", "AB-3"], "3"),
    ],
)
def test_round48_total_count_still_distinct_ids(row_ids, expected_total):
    """The canonical "(total)" value must remain distinct-ID count
    (``nunique``) after the disclosure row was added -- not the row
    count.  This guards against a regression where someone "fixes"
    the gap by changing the headline definition instead of adding
    the disclosure.
    """

    ab = _build_ab(row_ids=row_ids)
    rows = styling.build_summary_rows(
        {"AB_Detail_All": ab, "CSOne_Detail_All": pd.DataFrame()},
        {"customer_pulse": pd.DataFrame()},
    )
    by_label = dict(rows)
    assert by_label["Adoption barriers (total)"] == expected_total
