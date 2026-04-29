"""Round 43 / Phase 3 regression test.

Pin the renewal path's ``cm.build_portfolio_metrics(...)`` call to NOT pass
``extra_customer_frames`` or ``account_to_customer`` -- both inflate
``total_customers`` to the wider subscriptions universe (188 in the build-19
demo) while the validator at ``report_consistency.py:318-330`` enforces the
narrow ``count_customers(ab_df=, csone_df=, pulse_df=)`` universe (70).

Pre-fix the renewal path raised:

    Portfolio metric mismatch: total_customers=188 (Word headline) != 70
    (canonical AB ∪ CSOne ∪ Pulse universe).  The Word headline must
    mirror the Excel Summary row -- both derive from
    count_customers(ab_df=, csone_df=, pulse_df=).  If the Word path is
    using extra_frames / account_to_customer to widen the count, drop those
    args from the headline call site.

source-pinned to the build-19 demo run
``Renewal_Portfolio_All_Managers_Webex_Meetings_and_Messaging_90d_1777428729``.

Round 43 / Phase 3 dropped the two args.  The validator's per-section
defect/customer linkage still receives ``extra_frames`` / ``account_to_customer``
via the separate ``validate_report_consistency`` call below the PM build,
so wider counts remain available downstream -- only the headline narrows.
"""

from __future__ import annotations

import re
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


def test_renewal_pm_call_does_not_pass_extra_customer_frames() -> None:
    """The renewal ``cm.build_portfolio_metrics(...)`` MUST NOT pass
    ``extra_customer_frames=`` -- doing so widens ``total_customers`` past
    the canonical AB ∪ CSOne ∪ Pulse universe and reintroduces the build-19
    demo crash.
    """
    snippet = _renewal_pm_call_snippet()
    assert "extra_customer_frames" not in snippet, (
        "Round 43 / Phase 3: renewal cm.build_portfolio_metrics(...) must "
        "NOT pass extra_customer_frames=; doing so reintroduces the build-19 "
        f"demo total_customers=188 != 70 crash.  Current snippet: {snippet!r}"
    )


def test_renewal_pm_call_does_not_pass_account_to_customer() -> None:
    """Same contract for ``account_to_customer=``."""
    snippet = _renewal_pm_call_snippet()
    assert "account_to_customer" not in snippet, (
        "Round 43 / Phase 3: renewal cm.build_portfolio_metrics(...) must "
        "NOT pass account_to_customer=; doing so reintroduces the build-19 "
        f"demo total_customers crash.  Current snippet: {snippet!r}"
    )


def test_renewal_validator_still_receives_extra_frames() -> None:
    """The validator call BELOW the PM build SHOULD still receive
    ``extra_frames=`` / ``account_to_customer=`` -- those widen the
    per-section defect/customer linkage which is independent of the
    headline tile.  Only the headline narrows.
    """
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


def test_renewal_pm_narrowed_passes_validator_check() -> None:
    """Functional check: when the narrow PM (no extra_frames) is paired with
    a validator that ALSO sees no extra_frames in its total_customers
    derivation (Round 25 / Phase A contract), the total_customers gate
    passes.
    """
    import pandas as pd
    import canonical_metrics as cm
    from report_consistency import validate_report_consistency

    ab_df = pd.DataFrame({
        "ID": [f"AB-{i:03d}" for i in range(5)],
        "customer_name": [f"Customer {i}" for i in range(5)],
    })
    csone_df = pd.DataFrame({"customer_name": [f"Customer {i}" for i in range(3, 8)]})
    extra_subs = pd.DataFrame({"customer_name": [f"SubsOnly {i}" for i in range(20)]})

    # Round 43 / Phase 3 contract: the headline PM does NOT see extras.
    pm = cm.build_portfolio_metrics(
        ab_df=ab_df,
        csone_df=csone_df,
        risk_scale=cm.RISK_SCALE_0_TO_100,
    )
    # Validator can still see extras for per-section linkage; only the
    # ``total_customers`` parity check uses the narrow universe.
    result = validate_report_consistency(
        ab_df,
        csone_df,
        portfolio_metrics=pm,
        extra_frames=[extra_subs],
    )
    # No total_customers mismatch in the errors list.
    for err in (result.get("errors", []) or []):
        assert "total_customers" not in err, (
            "Round 43 / Phase 3 contract violated: validator still raised "
            f"total_customers mismatch ({err!r})."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
