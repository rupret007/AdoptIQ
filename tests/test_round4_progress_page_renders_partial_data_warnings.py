"""Round 4 / Phase 2.2 regression test.

The progress-page polling JS in ``app_simple.py`` must read
``data.partial_data_warnings`` and render a warning banner.  Round 3
persists the warnings on the analysis status; pre-Round-4 the browser
silently dropped them.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_progress_polling_reads_partial_data_warnings() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    # The progress route inlines a JS snippet that polls /api/status.
    # The Round 4 fix must reference data.partial_data_warnings (or
    # an equivalent destructure) inside the polling block.
    assert "partial_data_warnings" in src, (
        "Round 4 Phase 2.2: the progress page polling JS must consume "
        "data.partial_data_warnings so warnings persisted on the "
        "analysis status surface in the browser."
    )
