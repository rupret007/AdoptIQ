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
from source_shape_utils import assert_in_source, index_in_source

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
    idx = index_in_source(src, "Round 71 / Phase 4 (#19): Snowflake")
    assert idx > 0, (
        "Round 71 / Phase 4 (#19): the AP merge marker comment must "
        "exist near the drop_duplicates call site."
    )
    # Round 169 adds an observation-preserving union before this legacy
    # presentation merge.  Anchor on the actual stable-ID reconciliation
    # block instead of a fixed-size comment window, which can end at an
    # earlier mention of ``drop_duplicates`` as the source boundary grows.
    relative_reconcile_idx = index_in_source(src[idx:], "_r142_ap_with_id =")
    reconcile_idx = idx + relative_reconcile_idx
    assert reconcile_idx > idx, (
        "Round 71 / Phase 4 (#19): the populated-ID reconciliation block "
        "must follow the Snowflake-wins marker."
    )
    body = src[reconcile_idx : reconcile_idx + 1200]
    assert_in_source(body, "drop_duplicates", label="body")
    assert_in_source(body, "subset=", label="body")
    assert_in_source(body, "ID", label="body")
    assert_in_source(body, 'keep="last"', label="body")


def test_round71_ap_merge_has_lmd_tiebreak() -> None:
    """The AP merge MUST sort by LastModifiedDate before dedup so
    the most-recently-modified row survives even when LMD differs
    between the two sources."""
    src = _read_app_simple()
    assert_in_source(src, "_r71_lmd_candidates", label='src')
    assert_in_source(src, "_r71_lmd_sortkey", label='src')
    # The sort must be ascending so keep='last' picks the most-recent.
    # Locate the sort_values call inside the LMD branch.
    idx = index_in_source(src, "_r71_lmd_sortkey")
    assert idx > 0
    window = src[idx : idx + 1200]
    assert_in_source(window, "ascending=True", label='window')
    assert_in_source(window, "kind='stable'", label='window')


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
        assert_in_source(src, variant, label='src')


def test_round71_ap_merge_lmd_branch_falls_back_safely_on_exception() -> None:
    """If ``pd.to_datetime`` raises, the merge MUST still complete
    via plain ``keep='last'`` (the LMD step is best-effort)."""
    src = _read_app_simple()
    # The fallback must be a debug-level log, not an unhandled raise.
    assert_in_source(src, "Round 71 / #19: LMD sort skipped", label='src')
