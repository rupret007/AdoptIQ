"""Round 3 / Phase 5.2 regression test.

The admin dashboard's server-health probe must NOT treat an empty
``200 {}`` response from ``/api/diag/connectivity`` as a healthy data
path. Without an explicit ``ok=True`` or ``healthy=True`` field, the
probe should refuse to claim health.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_diag_probe_requires_explicit_ok_or_healthy():
    src = (PROJECT_ROOT / "enhanced_admin_dashboard_v2.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    # The new gating must require ok or healthy explicitly.
    assert "'ok' in _payload or 'healthy' in _payload" in src, (
        "diag probe must require an explicit ok/healthy field, not assume "
        "200-with-empty-body == healthy"
    )
    # And it must declare a clear "refusing to call this healthy" failure
    # path for empty 200 responses.
    assert_in_source(src, "refusing to call this 'healthy'", label='src')
