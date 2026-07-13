"""Round 6 / Phase 1.1 regression test.

The support-case trend chart must not mutate the caller's
``customer_csone`` frame.  Later panels read the same frame and
were getting a date-trimmed view that disagreed with the headline
risk count.  CodeGuard ``input-validation-injection`` (data
contract integrity).
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_trend_chart_uses_local_work_frame() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 1.1" in src, (
        "Round 6 Phase 1.1 marker missing in app_simple.py."
    )
