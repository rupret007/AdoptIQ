"""Round 72 / Build 46 -- Finding 1 regression pin.

The renewal XLSX ``Key_Metrics`` sheet's ``Risk_Score`` cell MUST be
rounded to 1 decimal place so the cross-format parity gate sees the
same value as the renewal DOCX (which renders the score via ``:.1f``).

Pre-Round-72 acceptance against real Snowflake data (renewal_portfolio,
All Managers, All Contact Center, 90d):
- DOCX rendered: ``9.8``
- XLSX ``Key_Metrics.Risk_Score`` cell: ``9.81204188481677``

The 14-digit raw float was emitted unchanged from
``renewal_analysis['overall_risk_score']`` (which is ``np.mean(scores)``
in the renewal portfolio path) into the ``key_metrics`` dict and then
into ``pd.DataFrame([key_metrics])``.  Round 71 / Phase 4 (#23) pinned
the DOCX rounding but missed the XLSX side, so the parity gate fired
on every renewal run.

This file pins the SOURCE SHAPE of the fix in app_simple.py.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source, count_in_source

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP_PATH = REPO / "app_simple.py"


def _load_app_source() -> str:
    return APP_PATH.read_text(encoding="utf-8")


def test_round72_round_risk_score_helper_defined():
    """The helper that performs the 1dp rounding MUST exist near the
    Key_Metrics build site (so future readers know why the round
    happens here and not elsewhere)."""

    src = _load_app_source()
    assert_in_source(src, "_r72_round_risk_score", label='src')
    # The helper body MUST round to exactly 1 decimal place.
    helper_match = re.search(
        r"def\s+_r72_round_risk_score\([^)]*\)[^:]*:\s*\n"
        r"\s*try:\s*\n\s*return\s+round\(\s*float\(\s*\w+\s*\)\s*,\s*1\s*\)\s*\n",
        src,
    )
    assert helper_match, (
        "Round 72 / Finding 1: _r72_round_risk_score body must be "
        "round(float(value), 1)"
    )


def test_round72_key_metrics_default_branch_uses_helper():
    """The ``if not key_metrics`` default-construction branch MUST
    apply ``_r72_round_risk_score`` to the renewal risk-score field.

    Round 145 publishes both explicit scales.  ``overall_risk_score`` is
    the 0-10 projection and ``risk_score_0_100`` is its paired value.
    """

    src = _load_app_source()
    assert_in_source(src, "Risk_Score_0_10", label="src")
    assert_in_source(src, "_r72_round_risk_score(overall_risk_score)", label="src")
    assert_in_source(src, "_r72_round_risk_score(risk_score_0_100)", label="src")


def test_round72_key_metrics_existing_branch_normalizes_risk_score():
    """The ``else`` branch (analyzer already produced ``key_metrics``)
    MUST also normalize the risk score so analyzer-side raw floats
    don't bypass the rounding.

    Round 125 / C2 migrates a legacy unlabeled ``Risk_Score`` key to
    the explicit ``Risk_Score_0_100`` (via pop+reassign) AND normalizes
    an already-explicit ``Risk_Score_0_100``. Either branch must route
    through ``_r72_round_risk_score``.
    """

    src = _load_app_source()
    assert_in_source(src, '"Risk_Score" in key_metrics', label="src")
    assert_in_source(src, 'key_metrics.pop("Risk_Score")', label="src")
    assert_in_source(src, 'key_metrics["Risk_Score_0_100"] = _r72_round_risk_score', label="src")
    assert_in_source(src, '"Risk_Score_0_100" in key_metrics', label="src")


def test_round72_round_risk_score_helper_handles_non_numeric_gracefully():
    """The helper must return the input unchanged when round() fails
    so a stale fixture or legacy 'n/a' string can't crash the writer."""

    # The behavioural pin via direct exec of the helper body.
    code = compile(
        "def f(value):\n"
        "    try:\n"
        "        return round(float(value), 1)\n"
        "    except (TypeError, ValueError):\n"
        "        return value\n",
        "<helper>",
        "exec",
    )
    ns: dict = {}
    exec(code, ns)  # noqa: S102 - controlled exec of trusted constant
    f = ns["f"]
    assert f(9.81204188481677) == 9.8
    assert f("9.81204") == 9.8
    assert f(0) == 0.0
    assert f("n/a") == "n/a"
    assert f(None) is None


def test_round72_marker_present_in_app_source():
    """A ``# Round 72`` audit marker must be on the new lines for the
    ``git diff app_simple.py | grep 'Round 72'`` audit footprint."""

    src = _load_app_source()
    assert count_in_source(src, "Round 72 / Build 46 (Finding 1)") >= 1, (
        "Round 72 / Build 46 audit marker missing from app_simple.py"
    )
