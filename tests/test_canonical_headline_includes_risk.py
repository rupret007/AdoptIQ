"""Round 3 / Phase 2.6 regression test.

When ``risk_profiles`` are passed to ``build_portfolio_metrics``, the
returned ``CANONICAL_HEADLINE``-shaped payload must include
``high_risk_customers`` and the per-band counts. Previously Ask AI
was calling this without ``risk_profiles`` so the prompt's
authoritative headline omitted risk numbers, and the model could
disagree with the dashboard.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import canonical_metrics as cm


def test_canonical_payload_includes_high_risk_customers_when_profiles_given():
    risk_profiles = {
        "Acme Corp": {"risk_band": "CRITICAL", "risk_score": 90},
        "Beta Inc": {"risk_band": "HIGH", "risk_score": 60},
        "Gamma LLC": {"risk_band": "MEDIUM", "risk_score": 40},
        "Delta Co": {"risk_band": "LOW", "risk_score": 20},
        "Epsilon Inc": {"risk_band": "HEALTHY", "risk_score": 5},
    }
    payload = cm.build_portfolio_metrics(
        ab_df=pd.DataFrame(),
        csone_df=pd.DataFrame(),
        risk_profiles=risk_profiles,
    )
    assert "high_risk_customers" in payload
    # CRITICAL + HIGH = 2
    assert payload["high_risk_customers"] == 2
    # Per-band counts also present so the chart can render
    # Critical vs High vs Medium vs Low vs Healthy honestly.
    assert payload["critical_risk_customers"] == 1
    assert payload["high_only_risk_customers"] == 1
    assert payload["medium_risk_customers"] == 1
    assert payload["low_risk_customers"] == 1
    assert payload["healthy_customers"] == 1


def test_canonical_payload_omits_risk_when_no_profiles():
    payload = cm.build_portfolio_metrics(
        ab_df=pd.DataFrame(),
        csone_df=pd.DataFrame(),
        risk_profiles=None,
    )
    assert "high_risk_customers" not in payload


def test_ask_ai_grounded_passes_risk_profiles():
    """Source guard: ``run_portfolio_grounded_ask_ai`` must pass
    ``risk_profiles`` (even an empty dict computed from the bundle)
    into ``cm.build_portfolio_metrics`` so the canonical headline
    table the LLM sees carries the same risk numbers as the UI."""
    src = (PROJECT_ROOT / "ask_ai_grounded.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    assert "risk_profiles=_risk_profiles_canon" in src, (
        "ask_ai_grounded must thread risk_profiles into build_portfolio_metrics"
    )
