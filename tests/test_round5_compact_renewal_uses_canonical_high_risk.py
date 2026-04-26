"""Round 5 / Phase 5.2 regression test.

``compact.add_renewal_recommendations_detailed`` must classify
high/moderate via the canonical ``cm.is_high_risk_profile`` helper
instead of a raw ``score >= 5.5`` cutoff that silently drifts from
``RISK_BAND_THRESHOLDS``.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_compact_renewal_uses_canonical_high_risk_helper() -> None:
    src = (REPO_ROOT / "compact_report_formatter.py").read_text(encoding="utf-8")
    assert "Round 5 / Phase 5.2" in src, (
        "Round 5 Phase 5.2 marker missing in compact_report_formatter.py."
    )
    assert "is_high_risk_profile" in src, (
        "Round 5 Phase 5.2: compact_report_formatter must call "
        "cm.is_high_risk_profile(...) instead of a raw 5.5 cutoff."
    )
