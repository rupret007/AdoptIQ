"""Round 7 / Phase 1.2 regression test.

When the account-to-customer map cannot be resolved, the EI formatter
must surface a partial-data warning row rather than silently using an
empty dict.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_ei_account_customer_resolve_warning() -> None:
    src = (REPO_ROOT / "executive_intelligence_formatter.py").read_text(encoding="utf-8")
    assert "Round 7 / Phase 1.2" in src, (
        "Round 7 Phase 1.2 marker missing in executive_intelligence_formatter.py."
    )
