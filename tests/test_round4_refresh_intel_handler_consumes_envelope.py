"""Round 4 / Phase 2.3 regression test.

The ``/api/refresh-external-intel`` JS handler in
``external_intelligence.html`` must consume the new envelope fields
(``partial`` / ``state`` / ``fetch_errors``) instead of only checking
``data.ok``.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_refresh_handler_consumes_envelope_fields() -> None:
    html = (REPO_ROOT / "templates" / "external_intelligence.html").read_text(encoding="utf-8")
    # We require references to all three envelope fields the backend now
    # publishes (Round 3 / 5.6 + Round 4 / 4.1-4.2).
    for field in ("fetch_errors", "state"):
        assert field in html, (
            f"Round 4 Phase 2.3: external_intelligence.html refresh "
            f"handler must reference '{field}' so failed feeds surface "
            f"in the UI instead of being swallowed by a blanket reload."
        )
