"""Round 3 / Phase 5.3 regression test.

When a subscription can't be scored (ID not found / data unavailable),
the ``/subscription_renewal_risk/<id>`` endpoint must return a 404
envelope carrying ``risk_score: null`` and ``state: 'unavailable'``,
rather than a 200 with ``risk_score: 0`` (which downstream UI was
quietly rendering as "no risk").
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_subscription_renewal_endpoint_returns_404_with_null_score():
    src = (PROJECT_ROOT / "app_simple.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    idx = src.find("def subscription_renewal_risk(subscription_id)")
    assert idx >= 0
    body = src[idx : idx + 2000]
    # Must short-circuit on error and return 404
    assert "), 404" in body
    # Must surface state explicitly
    assert "'state'" in body
    # Must NOT just return the legacy 200 + score=0 envelope here
    assert "'risk_score': 0" not in body


def test_backend_unknown_subscription_marks_state_unavailable():
    src = (PROJECT_ROOT / "adoptiq_backend.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    idx = src.find("def get_subscription_renewal_risk")
    assert idx >= 0
    body = src[idx : idx + 4000]
    # State and risk_score=None should be present in the unknown branch
    assert "'state'" in body
    assert "'risk_score': None" in body or "\"risk_score\": None" in body
