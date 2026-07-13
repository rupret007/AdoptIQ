"""Round 5 / Phase 6.2 regression test.

PII / customer-attributable strings (customer name, subscription IDs,
ARR figures) must be logged at DEBUG, not INFO, so they are not
captured by default in operator log streams.  CodeGuard ``logging``
and ``privacy-data-protection``.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_pii_log_lines_demoted_to_debug() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert "Round 5 / Phase 6.2" in src, (
        "Round 5 Phase 6.2 marker missing in adoptiq_backend.py."
    )
    # Verify no INFO-level log line includes "Searching subscriptions for customer:" etc.
    # (We accept the line under DEBUG.)
    bad = 'logger.info(f"[[SEARCH]] Searching subscriptions for customer:'
    assert bad not in src, (
        "Round 5 Phase 6.2: customer-name search log must be DEBUG not INFO."
    )
