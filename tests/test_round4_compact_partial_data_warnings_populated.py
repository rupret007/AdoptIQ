"""Round 4 / Phase 4.4 regression test.

The compact analysis path must populate ``partial_data_warnings`` on
fetch failures (mirroring the comprehensive branch).  Pre-Round-4
``partial_data_warnings`` was initialized to ``[]`` and then never
appended to, so compact runs always shipped an empty warning list
even when fetches partially failed.
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_compact_path_appends_partial_data_warnings() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    appends = list(re.finditer(r"partial_data_warnings\.append\(", src))
    # Round 4 introduces several explicit appends (CSConsole prefetch
    # failures, AB widening, ext_bugs / ext_incidents fetch failures,
    # etc.).  We require >= 3 distinct append sites as a smoke check.
    assert len(appends) >= 3, (
        "Round 4 Phase 4.4: app_simple.py must contain multiple "
        "partial_data_warnings.append(...) sites in the compact path "
        f"(found {len(appends)})."
    )
