"""Round 71 / Phase 4 (#19) -- Action Plans merge keeps Snowflake on conflict.

Pre-R71 the comprehensive XLSX's Action Plans merge between CSConsole
and Snowflake used ``drop_duplicates(subset=['ID'], keep='first')``.
Because CSConsole was concatenated FIRST, the CSConsole row always
won on conflict -- but CSConsole exports lag the Snowflake source-of-
truth by hours to days, so the operator saw stale status / due-date /
ownership data on every comprehensive run.

Round 71 / Phase 4 (#19):
- Switches the dedup to ``keep='last'`` (Snowflake-wins on equal
  LastModifiedDate, since Snowflake is concatenated AFTER CSConsole).
- Adds a LastModifiedDate tie-break: when both sources have the same
  ID AND different LastModifiedDate, the most-recently-modified row
  survives regardless of source.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_app_simple() -> str:
    return (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8", errors="replace")


def test_round71_ap_merge_uses_keep_last_not_first() -> None:
    """The dedup MUST use ``keep='last'`` (Snowflake wins on equal
    LMD) -- the pre-R71 ``keep='first'`` (CSConsole wins) is the
    bug condition."""
    src = _read_app_simple()
    # Locate the comprehensive AP merge section
    idx = src.find("Round 71 / Phase 4 (#19)")
    assert idx > 0, (
        "Round 71 / Phase 4 (#19): the AP merge marker comment must "
        "exist near the drop_duplicates call site."
    )
    body = src[idx : idx + 4000]
    assert "drop_duplicates(\n                            subset=['ID'], keep='last'" in body or (
        "drop_duplicates" in body and "keep='last'" in body
    ), (
        "Round 71 / Phase 4 (#19): the AP merge dedup must use "
        "keep='last' (so Snowflake wins on conflict)."
    )


def test_round71_ap_merge_has_lmd_tiebreak() -> None:
    """The AP merge MUST sort by LastModifiedDate before dedup so
    the most-recently-modified row survives even when LMD differs
    between the two sources."""
    src = _read_app_simple()
    assert "_r71_lmd_candidates" in src, (
        "Round 71 / Phase 4 (#19): the AP merge must define a "
        "_r71_lmd_candidates list (the recognised LMD column names)."
    )
    assert "_r71_lmd_sortkey" in src, (
        "Round 71 / Phase 4 (#19): the AP merge must materialise a "
        "_r71_lmd_sortkey column for the sort step."
    )
    # The sort must be ascending so keep='last' picks the most-recent.
    # Locate the sort_values call inside the LMD branch.
    idx = src.find("_r71_lmd_sortkey")
    assert idx > 0
    window = src[idx : idx + 1200]
    assert "ascending=True" in window, (
        "Round 71 / Phase 4 (#19): sort_values on _r71_lmd_sortkey must "
        "be ascending=True so keep='last' picks the most-recent row."
    )
    assert "kind='stable'" in window, (
        "Round 71 / Phase 4 (#19): sort_values must use "
        "kind='stable' so equal LMD ties fall back to original "
        "concat order (CSConsole first, Snowflake second)."
    )


def test_round71_ap_merge_lmd_candidate_list_covers_known_variants() -> None:
    """The LMD candidate list MUST include the documented variants
    so neither CSConsole nor Snowflake exports slip through."""
    src = _read_app_simple()
    expected_variants = [
        "'LastModifiedDate'",
        "'LASTMODIFIEDDATE'",
        "'LAST_MODIFIED_DATE'",
    ]
    for variant in expected_variants:
        assert variant in src, (
            f"Round 71 / Phase 4 (#19): LMD candidate list must include "
            f"{variant} (recognised column name across CSConsole / Snowflake)."
        )


def test_round71_ap_merge_lmd_branch_falls_back_safely_on_exception() -> None:
    """If ``pd.to_datetime`` raises, the merge MUST still complete
    via plain ``keep='last'`` (the LMD step is best-effort)."""
    src = _read_app_simple()
    # The fallback must be a debug-level log, not an unhandled raise.
    assert "Round 71 / #19: LMD sort skipped" in src, (
        "Round 71 / Phase 4 (#19): the LMD coercion must catch "
        "pd.to_datetime exceptions and fall through to plain "
        "keep='last' (logging at debug)."
    )
