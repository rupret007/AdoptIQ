"""Round 3 / Phase 4.2 regression test.

All ``compute_customer_risk_profile`` call sites must pass an explicit
``recent_window_days`` derived from the analysis horizon (``days``),
not silently default to a hard-coded constant. If a caller drops the
horizon, "recent activity" risk decay can disagree with the rest of
the report (e.g. a 90-day report scoring with a 30-day decay).
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

CALL_SITES = [
    PROJECT_ROOT / "app_simple.py",
    PROJECT_ROOT / "compact_report_formatter.py",
    PROJECT_ROOT / "adoptiq_backend.py",
    PROJECT_ROOT / "ask_ai_grounded.py",
]


def test_no_compute_risk_call_omits_recent_window_days():
    """Every textual call to ``compute_customer_risk_profile(`` in
    these modules must include a ``recent_window_days=`` keyword
    within ~600 characters of the call. We tolerate multi-line calls.
    """
    for path in CALL_SITES:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        idx = 0
        while True:
            idx = text.find("compute_customer_risk_profile(", idx)
            if idx < 0:
                break
            window = text[idx : idx + 800]
            assert "recent_window_days=" in window, (
                f"{path.name}: a compute_customer_risk_profile call near "
                f"offset {idx} does not pass recent_window_days=...:\n"
                f"{window[:400]}"
            )
            idx += len("compute_customer_risk_profile(")


def test_recent_window_days_derived_from_days_horizon():
    """Where ``days`` is in scope, ``recent_window_days`` should be
    derived from it (``int(days)``) rather than a hard-coded literal."""
    app_simple = (PROJECT_ROOT / "app_simple.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    # At least one call should derive from days.
    assert "recent_window_days=int(days)" in app_simple
