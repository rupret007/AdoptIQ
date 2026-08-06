"""Round 3 / Phase 5.1 regression test.

Both the comprehensive and the compact analysis paths must persist
``partial_data_warnings`` onto the in-memory ``status`` dict (and into
``status['results']`` when results are present) so the History page
and the Admin Diagnostics tile can show the same caveats the report
itself was generated under.
"""

from __future__ import annotations
from source_shape_utils import count_in_source

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_compact_and_comprehensive_persist_partial_warnings():
    src = (PROJECT_ROOT / "app_simple.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    # Both branches should set status['partial_data_warnings'] explicitly.
    occurrences = count_in_source(src, "status['partial_data_warnings']")
    assert occurrences >= 2, (
        "Expected partial_data_warnings to be persisted on at least the "
        f"compact + comprehensive status dicts; found {occurrences}"
    )
    # And both branches should also propagate into status['results']
    # so the API surface (used by the History page) shows the warnings.
    results_occurrences = count_in_source(src, "status['results']['partial_data_warnings']")
    assert results_occurrences >= 2, (
        "Expected partial_data_warnings to be propagated to "
        f"status['results']; found {results_occurrences}"
    )
