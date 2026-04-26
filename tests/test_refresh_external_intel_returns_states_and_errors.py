"""Round 3 / Phase 5.6 regression test.

``/api/refresh-external-intel`` must return per-feed ``state``
(present / empty / failed) and a nested ``fetch_errors`` map alongside
the legacy counts, so the Refresh UI / automations can distinguish
"truly zero" from "couldn't reach the upstream".
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_refresh_external_intel_envelope_includes_states_and_errors():
    src = (PROJECT_ROOT / "app_simple.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    idx = src.find("def refresh_external_intel")
    assert idx >= 0
    body = src[idx : idx + 4000]
    # Per-feed state fields
    for key in ("'incidents_state'", "'bugs_state'", "'maintenances_state'"):
        assert key in body, f"refresh-external-intel envelope missing {key}"
    # Nested fetch_errors map
    assert "'fetch_errors'" in body
    # Partial flag
    assert "'partial'" in body
    # State vocabulary
    for state in ("'present'", "'empty'", "'failed'"):
        assert state in body, (
            f"refresh-external-intel state vocabulary missing {state}"
        )
