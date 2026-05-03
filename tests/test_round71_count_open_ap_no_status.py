"""Round 71 / Phase 4 (#20) -- count_open_action_plans does not over-count.

Pre-R71 ``count_open_action_plans`` returned ``int(len(ap_df))`` when
the ``ap_df`` had no recognized status column (``STATUS_C`` /
``AP_STATUS_C`` / ``Status`` / ``STATUS`` / ``status`` /
``case_status_norm`` / ``status_norm``).  This silently inflated the
headline KPI to the row total whenever the Snowflake projection had
stripped the STATUS_C column or a bare CSConsole sheet was missing
every status candidate column.

Round 71 / Phase 4 (#20) returns 0 instead and emits a WARN so the
operator can see the missing-column condition in the analysis log.
"""

from __future__ import annotations

import logging

import pandas as pd
import pytest

import canonical_metrics


def test_round71_count_open_aps_returns_zero_when_no_status_column() -> None:
    """A non-empty ap_df with NO recognized status column MUST return
    0 (not ``len(ap_df)``)."""
    df = pd.DataFrame({
        "ID": ["A1", "A2", "A3"],
        "Title": ["plan a", "plan b", "plan c"],
        # No STATUS_C / Status / etc.
    })
    result = canonical_metrics.count_open_action_plans(ab_df=None, ap_df=df)
    assert result == 0, (
        f"Round 71 / Phase 4 (#20): non-empty ap_df with no status "
        f"column must return 0 (got {result}).  Pre-R71 returned len(ap_df)=3."
    )


def test_round71_count_open_aps_logs_warn_when_no_status_column(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The fallback MUST log a WARN naming the missing-column condition."""
    df = pd.DataFrame({"ID": ["A1"], "Title": ["plan a"]})
    with caplog.at_level(logging.WARNING, logger="canonical_metrics"):
        canonical_metrics.count_open_action_plans(ab_df=None, ap_df=df)
    msg = "\n".join(rec.message for rec in caplog.records)
    assert "Round 71 / Phase 4 (#20)" in msg, (
        "Round 71 / Phase 4 (#20): missing-column branch must log "
        "with the round marker so operators can correlate the count."
    )
    assert "no recognized status column" in msg, (
        "Round 71 / Phase 4 (#20): WARN message must explain the "
        "missing-column condition."
    )


def test_round71_count_open_aps_still_works_when_status_column_present() -> None:
    """Negative control: when STATUS_C IS present, the helper must
    still return the correct open count."""
    df = pd.DataFrame({
        "ID": ["A1", "A2", "A3", "A4"],
        "STATUS_C": ["In Progress", "Closed", "Completed - Successful", "Open"],
    })
    result = canonical_metrics.count_open_action_plans(ab_df=None, ap_df=df)
    # In Progress (open) + Open (open) = 2; Closed + Completed = 2 closed.
    assert result == 2, (
        f"Round 71 / Phase 4 (#20): the regression fix must NOT break "
        f"the happy path -- expected 2 open APs; got {result}."
    )


def test_round71_count_open_aps_returns_zero_for_empty_ap_df() -> None:
    """Negative control: empty ap_df + None ab_df returns 0."""
    df = pd.DataFrame()
    result = canonical_metrics.count_open_action_plans(ab_df=None, ap_df=df)
    assert result == 0


def test_round71_module_logger_present() -> None:
    """The R71 fix added a module-local logger.  Confirm it exists."""
    assert hasattr(canonical_metrics, "logger"), (
        "Round 71 / Phase 4 (#20): canonical_metrics must define a "
        "module-local logger so the WARN emission has a stable name."
    )
    assert canonical_metrics.logger.name == "canonical_metrics"
