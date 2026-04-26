"""Round 3 / Phase 5.5 regression test.

The admin dashboard's data-path tile is fed by
``/api/diag/connectivity``, which only probes DNS → TCP/TLS →
AppRole → secret-read → Snowflake. It does NOT exercise the CircuIT
LLM. The previous docstring/UI claimed the tile included "LLM
dependencies", which gave readers false confidence that AI summary
calls were healthy whenever Snowflake was reachable.

The fix is to (1) clarify the docstring and (2) NOT label the LLM
as part of this probe.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_check_server_health_status_docstring_excludes_llm_claim():
    src = (PROJECT_ROOT / "enhanced_admin_dashboard_v2.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    idx = src.find("def get_server_status")
    assert idx >= 0
    body = src[idx : idx + 4000]
    # Must explicitly disclaim that LLM is part of the probe
    assert (
        "CircuIT/LLM health is **not** part of the data-path tile" in body
        or "LLM" in body and "not** part" in body
    ), "docstring should explicitly state LLM is not covered by the probe"
