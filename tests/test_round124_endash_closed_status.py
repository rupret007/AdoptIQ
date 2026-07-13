"""Round 124 / F1 -- Unicode-dash fold in the open-action-plan status check.

CSConsole exports the cancelled status as ``"Closed \u2013 Cancelled"`` with a
Unicode en-dash (U+2013). ``_AP_CLOSED_STATUSES`` stores the ASCII-hyphen form
``"closed - cancelled"``, so before R124 the en-dash rows leaked into the OPEN
count (Build 92 Comprehensive showed ``Action plans (open) = 119`` when the true
open count was 116 -- three "Closed \u2013 Cancelled" rows mis-counted).
"""

import pandas as pd

import canonical_metrics as cm


def _ap_frame(statuses):
    return pd.DataFrame({"STATUS_C": list(statuses)})


def test_endash_closed_cancelled_counts_as_closed():
    # 3 cancelled (en-dash), 2 genuinely open.
    df = _ap_frame(
        [
            "Closed \u2013 Cancelled",
            "Closed \u2013 Cancelled",
            "Closed \u2013 Cancelled",
            "New Request",
            "On Track",
        ]
    )
    assert cm.count_open_action_plans(None, df) == 2


def test_ascii_hyphen_still_closed():
    df = _ap_frame(["Closed - Cancelled", "On Track"])
    assert cm.count_open_action_plans(None, df) == 1


def test_em_dash_and_minus_also_folded():
    df = _ap_frame(
        [
            "Completed \u2014 Successful",  # em-dash
            "Completed \u2212 Unsuccessful",  # minus sign
            "On Hold",  # genuinely open
        ]
    )
    # Both completed rows are closed; only "On Hold" is open.
    assert cm.count_open_action_plans(None, df) == 1


def test_build92_repro_119_vs_116():
    # Exact Build 92 Comprehensive status distribution.
    statuses = (
        ["Completed-Successful"] * 429
        + ["Completed-Unsuccessful"] * 5
        + ["Closed \u2013 Cancelled"] * 3  # en-dash
        + ["New Request"] * 62
        + ["On Track"] * 49
        + ["Off Trajectory"] * 2
        + ["On Hold"] * 3
    )
    # Note: "Completed-Successful" (no spaces around dash) is NOT in the closed
    # set, so those 429 + 5 remain "open" under the canonical set -- the only
    # thing R124 changes is folding the 3 en-dash cancelled rows to closed.
    open_count = cm.count_open_action_plans(None, _ap_frame(statuses))
    open_without_endash_fix = 62 + 49 + 2 + 3 + 429 + 5 + 3  # 553 if cancelled leaked
    open_with_endash_fix = 62 + 49 + 2 + 3 + 429 + 5  # 550 cancelled now closed
    assert open_count == open_with_endash_fix
    assert open_count == open_without_endash_fix - 3


def test_normalizer_folds_dashes():
    assert cm._normalize_ap_status_for_open_check("Closed \u2013 Cancelled") == "closed - cancelled"
    assert cm._normalize_ap_status_for_open_check("Closed \u2014 Cancelled") == "closed - cancelled"
    assert cm._normalize_ap_status_for_open_check("Closed   \u2013   Cancelled") == "closed - cancelled"
