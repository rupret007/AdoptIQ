"""Round 3 / Phase 4.7 regression test.

Renewal "expiring soon" check must compare on UTC *calendar dates*
(not instantaneous ``datetime.now()`` values) so a contract whose
``SERVICE_END_DATE`` equals "today UTC" is classified consistently
regardless of the report host's local timezone.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_advanced_renewal_uses_utc_date_for_expiring_soon():
    src = (PROJECT_ROOT / "advanced_renewal_analyzer.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    # Locate the Phase 4.7 fix block specifically (commented as such).
    idx = src.find("Phase 4.7")
    assert idx >= 0, "Expected a 'Phase 4.7' anchor comment in advanced_renewal_analyzer"
    window = src[idx : idx + 1500]
    assert "pd.Timestamp.utcnow()" in window, (
        "Expiring-soon check must anchor 'today' to UTC, not local time"
    )
    assert ".date()" in window, (
        "Expiring-soon check must compare on .date() (calendar day), "
        "not instantaneous Timestamp values"
    )
    # The legacy ``datetime.now() + timedelta(days=90)`` form must be gone
    # from this window.
    assert "datetime.now() + timedelta(days=90)" not in window
