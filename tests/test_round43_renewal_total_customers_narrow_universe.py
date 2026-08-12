"""Renewal customer-universe regression tests.

Round 162.1 supersedes the Round-43 narrow-detail contract: the portfolio
headline, risk rows, and validator now use every applicable scoped source.
"""

from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
APP_SIMPLE = REPO_ROOT / "app_simple.py"


def _read() -> str:
    return APP_SIMPLE.read_text(encoding="utf-8")


def _renewal_pm_call_snippet() -> str:
    """Locate the renewal ``cm.build_portfolio_metrics(...)`` call by
    anchoring on the unique ``risk_profiles=_ren_risk_profiles`` kwarg."""
    src = _read()
    anchor = "risk_profiles=_ren_risk_profiles"
    idx = src.find(anchor)
    assert idx > 0, "could not locate renewal cm.build_portfolio_metrics call"
    # Walk backwards to the opening ``cm.build_portfolio_metrics(`` and forward
    # to the matching ``)``.
    open_idx = src.rfind("cm.build_portfolio_metrics(", 0, idx)
    assert open_idx > 0, "could not locate the open paren of the renewal PM call"
    # Find the matching close paren by counting nesting.
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


def test_renewal_pm_call_passes_extra_customer_frames() -> None:
    snippet = _renewal_pm_call_snippet()
    assert "extra_customer_frames=_ren_extra_frames" in snippet


def test_renewal_pm_call_passes_account_to_customer() -> None:
    snippet = _renewal_pm_call_snippet()
    assert "account_to_customer=_ren_account_to_customer" in snippet


def test_renewal_validator_still_receives_extra_frames() -> None:
    """The validator must use the same all-source inputs as the PM."""
    src = _read()
    # The renewal validator call carries ``portfolio_metrics=renewal_portfolio_metrics``.
    anchor = "portfolio_metrics=renewal_portfolio_metrics"
    idx = src.find(anchor)
    assert idx > 0
    # The validator call body extends ~600 chars in either direction.
    body = src[max(0, idx - 600):idx + 600]
    assert "extra_frames=_ren_extra_frames" in body, (
        "renewal validator call must STILL pass extra_frames=_ren_extra_frames "
        "(only the PM build narrows; per-section linkage stays wide)."
    )
    assert "account_to_customer=_ren_account_to_customer" in body, (
        "renewal validator call must STILL pass "
        "account_to_customer=_ren_account_to_customer."
    )
    assert "include_all_customer_sources=True" in body


def test_renewal_all_source_pm_passes_validator_check() -> None:
    import pandas as pd
    import canonical_metrics as cm
    from report_consistency import validate_report_consistency

    ab_df = pd.DataFrame({
        "ID": [f"AB-{i:03d}" for i in range(5)],
        "customer_name": [f"Customer {i}" for i in range(5)],
    })
    csone_df = pd.DataFrame({"customer_name": [f"Customer {i}" for i in range(3, 8)]})
    extra_subs = pd.DataFrame({"customer_name": [f"SubsOnly {i}" for i in range(20)]})

    # Round 162.1 contract: the headline PM and validator both see extras.
    pm = cm.build_portfolio_metrics(
        ab_df=ab_df,
        csone_df=csone_df,
        risk_scale=cm.RISK_SCALE_0_TO_100,
        extra_customer_frames=[extra_subs],
    )
    result = validate_report_consistency(
        ab_df,
        csone_df,
        portfolio_metrics=pm,
        extra_frames=[extra_subs],
        include_all_customer_sources=True,
    )
    # No total_customers mismatch in the errors list.
    for err in (result.get("errors", []) or []):
        assert "total_customers" not in err, (
            "Round 43 / Phase 3 contract violated: validator still raised "
            f"total_customers mismatch ({err!r})."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
