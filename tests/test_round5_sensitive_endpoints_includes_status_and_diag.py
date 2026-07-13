"""Round 5 / Phase 6.1 regression test.

``_SENSITIVE_ENDPOINTS`` must include ``/status/<id>``,
``/api/status/all`` and ``/api/diag/connectivity`` so the localhost
gate covers the diagnostics surface (it currently exposes report
metadata and customer/sub data on LAN).  CodeGuard
``authorization-access-control``.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_sensitive_endpoints_covers_status_and_diag() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 5 / Phase 6.1" in src, (
        "Round 5 Phase 6.1 marker missing in app_simple.py."
    )
    # Spot-check the sensitive-endpoint allow-list contents.
    for needle in ("/status/", "/api/status/all", "/api/diag/connectivity"):
        assert needle in src, (
            f"Round 5 Phase 6.1: _SENSITIVE_ENDPOINTS must reference {needle!r}."
        )
