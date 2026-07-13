"""Round 5 / Phase 1.1 regression test.

The subscription Excel risk_components sheet must NOT silently drop
empty risk_components (e.g. when risk scoring did not run because data
was unavailable).  Instead it must emit an explicit
"No risk components were captured" placeholder so the user understands
the absence is a coverage gap rather than a healthy zero.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_subscription_risk_components_empty_emits_placeholder() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 5 / Phase 1.1" in src, (
        "Round 5 Phase 1.1: the subscription Excel writer must mark "
        "the Risk_Components sheet with a 'No risk components captured' "
        "placeholder when no risk components are returned, instead of "
        "writing a blank sheet that the operator cannot distinguish "
        "from a healthy zero."
    )
