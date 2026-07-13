"""Round 4 / Phase 6.2 regression test.

When canonical risk profiles are computed for a subset of the
customer universe (the 200-customer cap), the
``CANONICAL_HEADLINE`` block must include a
``risk_profiles_coverage`` line so the model knows risk-derived
counts are a lower bound, not an authoritative population figure.
"""
from __future__ import annotations

import pathlib


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_canonical_headline_discloses_partial_risk_coverage() -> None:
    src = (REPO_ROOT / "ask_ai_grounded.py").read_text(encoding="utf-8")
    assert "risk_profiles_coverage" in src, (
        "Round 4 Phase 6.2: CANONICAL_HEADLINE rendering must include "
        "a 'risk_profiles_coverage' line (FULL / PARTIAL / NONE) so "
        "the LLM knows risk-derived counts are conditional on coverage."
    )
    # Pin all three coverage tiers so future refactors keep the disclosure.
    for tier in ("FULL", "PARTIAL", "NONE"):
        assert tier in src, (
            f"Round 4 Phase 6.2: 'risk_profiles_coverage: {tier}' "
            f"branch must be present."
        )
