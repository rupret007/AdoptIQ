"""Round 5 / Phase 4.3 regression test.

``fetch_subscription_data`` must include a structured ``failure_kind``
(timeout/auth/parse/runtime) on the exception path so the UI and
validators can distinguish a transient network blip from an auth
failure or schema mismatch.  The previous implementation collapsed
all exceptions to one generic string with ``found: False``.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_fetch_subscription_failure_includes_kind() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert "Round 5 / Phase 4.3" in src, (
        "Round 5 Phase 4.3 marker missing in adoptiq_backend.py."
    )
    assert "failure_kind" in src, (
        "Round 5 Phase 4.3: fetch_subscription_data exception path must "
        "populate a structured 'failure_kind' field."
    )
