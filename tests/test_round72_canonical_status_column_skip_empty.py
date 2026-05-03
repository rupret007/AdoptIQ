"""Round 72 / Build 46 -- Finding 2 (root cause) regression pin.

After the initial Round 72 fix routed leader_report_generator.py's
``open_ab_count`` through ``cm.count_open_barriers(abs_df)``, the
docx began writing ``"Open Adoption Barriers: 0"`` -- worse than the
pre-fix 65 raw row count.  Live diagnostic instrumentation against
real Snowflake data showed:

    Angelica Hernandez Becerra:
        STATUS_C values=['Open','Open','Open','Open','Open']
        AB_STATUS_C values=[NaN, NaN, NaN, NaN, NaN]
        cm.count_open_barriers(abs_df) returned 0

The leader-pipeline AB extract carries BOTH ``STATUS_C`` (the
canonical Salesforce status field) AND ``AB_STATUS_C`` (a legacy
sister-table join that is empty in this Snowflake schema).  Pre-R72
the canonical helper picked the FIRST candidate present in the
DataFrame, in the literal tuple order ``("AB_STATUS_C", "STATUS_C",
"Status", "STATUS")`` -- so ``AB_STATUS_C`` won every time it was
present, and its all-NaN values normalized to "Unknown" en masse.

This file pins the FIX:
- ``_select_first_populated_status_column`` skips columns whose
  ``str.strip().str.len() > 0`` reduction is all-False.
- ``count_open_barriers`` and ``count_closed_barriers`` reorder
  the candidate tuple to put ``STATUS_C`` ahead of ``AB_STATUS_C``
  (matches the ``data_normalization.LIKELY_STATUS_COLS`` priority).
- A pure-``AB_STATUS_C`` frame still works (back-compat).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

import canonical_metrics as cm

REPO = Path(__file__).resolve().parent.parent
CM_PATH = REPO / "canonical_metrics.py"


def _load_cm_source() -> str:
    return CM_PATH.read_text(encoding="utf-8")


def test_select_helper_picks_status_c_when_ab_status_c_is_all_na():
    """Repro of the leader-pipeline shape -- both columns present, only
    STATUS_C is populated."""

    df = pd.DataFrame(
        {
            "ID": [f"AB-{i:04d}" for i in range(5)],
            "STATUS_C": ["Open", "Open", "Open", "Open", "Open"],
            "AB_STATUS_C": [None, None, None, None, None],
        }
    )
    chosen = cm._select_first_populated_status_column(
        df, ("STATUS_C", "AB_STATUS_C", "Status", "STATUS")
    )
    assert chosen == "STATUS_C", (
        "Round 72 / F2 root cause: the helper must pick STATUS_C when "
        "AB_STATUS_C is present-but-all-NaN (the leader Snowflake AB "
        "extract shape)."
    )


def test_select_helper_returns_none_when_all_candidates_blank():
    df = pd.DataFrame(
        {
            "ID": ["AB-0001", "AB-0002"],
            "STATUS_C": [None, None],
            "AB_STATUS_C": ["", ""],
        }
    )
    chosen = cm._select_first_populated_status_column(
        df, ("STATUS_C", "AB_STATUS_C", "Status", "STATUS")
    )
    assert chosen is None


def test_select_helper_falls_through_to_ab_status_c_when_status_c_missing():
    """A pipeline that only carries AB_STATUS_C must still work."""

    df = pd.DataFrame(
        {
            "ID": ["AB-0001", "AB-0002"],
            "AB_STATUS_C": ["Open", "Closed"],
        }
    )
    chosen = cm._select_first_populated_status_column(
        df, ("STATUS_C", "AB_STATUS_C", "Status", "STATUS")
    )
    assert chosen == "AB_STATUS_C"


def test_select_helper_skips_blank_string_only_column():
    """Columns containing only whitespace strings count as empty."""

    df = pd.DataFrame(
        {
            "ID": ["AB-0001", "AB-0002"],
            "STATUS_C": ["   ", " "],
            "AB_STATUS_C": ["Open", "Closed"],
        }
    )
    chosen = cm._select_first_populated_status_column(
        df, ("STATUS_C", "AB_STATUS_C", "Status", "STATUS")
    )
    assert chosen == "AB_STATUS_C", (
        "Whitespace-only STATUS_C must not win over a populated "
        "AB_STATUS_C; otherwise downstream normalization sees "
        "all-Unknown and silently zeros the count."
    )


def test_select_helper_skips_missing_columns_silently():
    """Non-existent candidates must not raise."""

    df = pd.DataFrame(
        {
            "ID": ["AB-0001"],
            "Status": ["Open"],
        }
    )
    chosen = cm._select_first_populated_status_column(
        df, ("STATUS_C", "AB_STATUS_C", "Status", "STATUS")
    )
    assert chosen == "Status"


def test_count_open_barriers_returns_5_on_leader_pipeline_shape():
    """The exact bug: 5 STATUS_C='Open' rows + 5 AB_STATUS_C=NaN rows
    must yield count=5, not 0.  This is Angelica Hernandez Becerra's
    per-CSSM frame from the live diagnostic run."""

    df = pd.DataFrame(
        {
            "ID": [f"AB-{i:04d}" for i in range(5)],
            "STATUS_C": ["Open", "Open", "Open", "Open", "Open"],
            "AB_STATUS_C": [None, None, None, None, None],
        }
    )
    assert cm.count_open_barriers(df) == 5, (
        "Round 72 / F2 root cause: the leader-pipeline AB extract "
        "carries STATUS_C with real values AND a present-but-blank "
        "AB_STATUS_C.  Pre-R72 the canonical helper picked "
        "AB_STATUS_C first and returned 0; the fix MUST return 5."
    )


def test_count_closed_barriers_returns_2_on_leader_pipeline_shape():
    """Symmetric to the open-side fix: closed denominator must also
    skip the blank AB_STATUS_C and read STATUS_C."""

    df = pd.DataFrame(
        {
            "ID": [f"AB-{i:04d}" for i in range(5)],
            "STATUS_C": ["Open", "Open", "Open", "Resolved", "Cancelled"],
            "AB_STATUS_C": [None, None, None, None, None],
        }
    )
    # Resolved + Cancelled both normalize to "Closed" via
    # CLOSED_STATUS_PATTERNS.
    assert cm.count_closed_barriers(df) == 2


def test_count_open_barriers_back_compat_pure_ab_status_c():
    """A frame carrying ONLY AB_STATUS_C (legacy CSV import shape)
    must still produce a correct count -- the fix MUST NOT
    regress that path."""

    df = pd.DataFrame(
        {
            "ID": ["AB-0001", "AB-0002", "AB-0003"],
            "AB_STATUS_C": ["Open", "Open", "Closed"],
        }
    )
    assert cm.count_open_barriers(df) == 2
    assert cm.count_closed_barriers(df) == 1


def test_count_open_barriers_back_compat_status_norm_short_circuit():
    """If a frame carries ``case_status_norm`` (the lifecycle-fields
    pre-pass output) the helper must use it directly without
    consulting the candidate list -- preserving the Round 4 / Phase
    3.4 contract."""

    df = pd.DataFrame(
        {
            "ID": ["AB-0001", "AB-0002", "AB-0003"],
            "case_status_norm": ["Open", "Closed", "Open"],
            # An adversarial AB_STATUS_C that would lie if the helper
            # ignored the precomputed norm column.
            "AB_STATUS_C": [None, None, None],
        }
    )
    assert cm.count_open_barriers(df) == 2


def test_count_open_barriers_status_c_priority_over_ab_status_c_both_populated():
    """When BOTH columns are populated, STATUS_C wins (it is the
    canonical Salesforce status field; AB_STATUS_C is a legacy
    sister-table join)."""

    df = pd.DataFrame(
        {
            "ID": ["AB-0001", "AB-0002"],
            "STATUS_C": ["Open", "Open"],
            "AB_STATUS_C": ["Closed", "Closed"],
        }
    )
    assert cm.count_open_barriers(df) == 2, (
        "STATUS_C must win when both columns are populated -- it is "
        "the canonical Salesforce field per LIKELY_STATUS_COLS order."
    )


def test_round72_marker_present_in_canonical_metrics_source():
    """The ``# Round 72`` audit marker must be on the changed lines."""

    src = _load_cm_source()
    assert "Round 72 / Build 46 (Finding 2)" in src, (
        "Round 72 audit marker missing from canonical_metrics.py; "
        "convention is to drop a # Round N comment on changed lines."
    )
