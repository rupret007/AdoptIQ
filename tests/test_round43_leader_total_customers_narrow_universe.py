"""Round 43 / Phase 4 regression test.

Same contract as Phase 3, but for the leader path's
``cm.build_portfolio_metrics(...)`` call at ``app_simple.py:20171-20177``.

Pre-fix the leader path emitted (warning, not crash, because the leader's
consistency check is wrapped in ``try/except`` that downgrades to
``logger.warning``):

    [[CONSISTENCY]] Leader consistency check skipped: Portfolio metric
    mismatch: total_customers=40 (Word headline) != 46 (canonical AB ∪
    CSOne ∪ Pulse universe).

source-pinned to the build-19 demo run ``Leader_Brian_Frazier_90d_1777428669``.

Round 43 / Phase 4 dropped ``extra_customer_frames=_leader_extra_frames`` and
``account_to_customer=_leader_a2c`` from the call so the leader headline
narrows to the canonical universe.  The validator at L20209 still receives
both kwargs for per-section defect/customer linkage.
"""

from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
APP_SIMPLE = REPO_ROOT / "app_simple.py"


def _read() -> str:
    return APP_SIMPLE.read_text(encoding="utf-8")


def _leader_pm_call_snippet() -> str:
    """Locate the leader ``cm.build_portfolio_metrics(...)`` call by
    anchoring on the unique ``ab_df=agg_ab`` kwarg."""
    src = _read()
    anchor = "ab_df=agg_ab"
    idx = src.find(anchor)
    assert idx > 0, "could not locate leader cm.build_portfolio_metrics call"
    open_idx = src.rfind("cm.build_portfolio_metrics(", 0, idx)
    assert open_idx > 0, "could not locate the open paren of the leader PM call"
    depth = 0
    end = open_idx
    for j in range(open_idx, min(len(src), open_idx + 4000)):
        ch = src[j]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                end = j + 1
                break
    return src[open_idx:end]


def test_leader_pm_call_does_not_pass_extra_customer_frames() -> None:
    snippet = _leader_pm_call_snippet()
    assert "extra_customer_frames" not in snippet, (
        "Round 43 / Phase 4: leader cm.build_portfolio_metrics(...) must "
        "NOT pass extra_customer_frames=; doing so reintroduces the build-19 "
        f"demo total_customers warning.  Current snippet: {snippet!r}"
    )


def test_leader_pm_call_does_not_pass_account_to_customer() -> None:
    snippet = _leader_pm_call_snippet()
    assert "account_to_customer" not in snippet, (
        "Round 43 / Phase 4: leader cm.build_portfolio_metrics(...) must "
        "NOT pass account_to_customer=; doing so reintroduces the build-19 "
        f"demo total_customers warning.  Current snippet: {snippet!r}"
    )


def test_leader_validator_still_receives_extra_frames_and_a2c() -> None:
    """The validator call MUST still receive ``extra_frames=_leader_extra_frames``
    and ``account_to_customer=_leader_a2c`` so per-section defect/customer
    linkage stays wide.  Only the headline narrows.
    """
    src = _read()
    anchor = "portfolio_metrics=_leader_portfolio_metrics"
    idx = src.find(anchor)
    assert idx > 0
    body = src[max(0, idx - 800):idx + 200]
    assert "extra_frames=_leader_extra_frames" in body, (
        "leader validator call must STILL pass extra_frames=_leader_extra_frames"
    )
    assert "account_to_customer=_leader_a2c" in body, (
        "leader validator call must STILL pass account_to_customer=_leader_a2c"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
