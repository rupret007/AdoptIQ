"""Round 3 / Phase 3.1 regression test.

The subscription lookup must be deterministic when multiple DSM rows
share a ``SUBSCRIPTION_ID``. The fix replaces a bare ``LIMIT 1`` with
``ORDER BY MODIFIED_DATE DESC NULLS LAST, CREATED_DATE DESC NULLS
LAST, ACCOUNT_ID_C LIMIT 5`` and warns when the candidates resolve
to multiple distinct ``ACCOUNT_ID_C`` values.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_subscription_lookup_uses_deterministic_order_by():
    src = (PROJECT_ROOT / "adoptiq_backend.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    assert "ORDER BY MODIFIED_DATE DESC NULLS LAST" in src, (
        "subscription lookup must order by MODIFIED_DATE DESC NULLS LAST"
    )
    assert "CREATED_DATE DESC NULLS LAST" in src, (
        "subscription lookup must include CREATED_DATE DESC NULLS LAST tiebreaker"
    )
    # The plain "LIMIT 1" with no ORDER BY pattern must not have come back.
    # We cannot just grep for "LIMIT 1" (used elsewhere); instead check for
    # the specific anti-pattern: SUBSCRIPTION_ID = %s ... LIMIT 1 with no ORDER.
    sub_block_marker = "WHERE SUBSCRIPTION_ID = %s"
    assert sub_block_marker in src
    idx = src.find(sub_block_marker)
    snippet = src[idx : idx + 800]
    assert "ORDER BY" in snippet, (
        "subscription lookup snippet missing ORDER BY"
    )


def test_subscription_lookup_warns_on_distinct_accounts(caplog):
    """Source-level guard: the lookup must log a WARNING when multiple
    DSM rows resolve to distinct ``ACCOUNT_ID_C`` values for the
    same subscription. (The runtime path requires Snowflake; we
    verify the warning string is present in source so a future
    refactor cannot silently drop it.)"""
    src = (PROJECT_ROOT / "adoptiq_backend.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    assert "distinct ACCOUNT_ID_C values" in src
