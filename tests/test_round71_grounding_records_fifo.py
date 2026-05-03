"""Round 71 / Phase 5 (#24) -- grounding rejection records use FIFO trim.

Pre-R71 ``_r64_record_grounding_outcome`` only appended rejection
records while ``len(records) < _R64_MAX_GROUNDING_RECORDS=50``.  Once
the cap was reached, the helper silently dropped every later
rejection -- the operator looking at ``/api/grounding-diagnostics``
for a long-running multi-customer comprehensive report only saw the
FIRST 50 rejections, even though ``rejection_summary.rejected`` kept
ticking up.

Round 71 / Phase 5 (#24) appends every rejection and trims the HEAD
of the list when the cap is exceeded so the MOST RECENT rejections
survive.  The summary running count is unchanged -- only the
per-record list is bounded.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Dict, List


REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_record_helper():
    """Import the helper directly from app_simple via importlib so we
    don't pull the full Flask app into pytest."""
    # Use a lightweight import path that doesn't trigger flask routes.
    # We just verify source-shape and exercise the helper via the
    # module-loaded function.
    import app_simple  # noqa: PLC0415
    return app_simple._r64_record_grounding_outcome, app_simple._R64_MAX_GROUNDING_RECORDS


def _fire_rejection(record_fn, status, marker: str) -> None:
    record_fn(
        status,
        scope="customer_narrative",
        rejected=True,
        failures=["ungrounded_number"],
        sample_offending={"marker": marker},
        briefing_excerpt="b",
        narrative_excerpt="n",
        customer_name=f"cust_{marker}",
        narrative_length=100,
    )


def test_round71_records_list_capped_at_max() -> None:
    """The records list MUST not exceed _R64_MAX_GROUNDING_RECORDS."""
    record_fn, cap = _load_record_helper()
    status: Dict[str, Any] = {"analysis_id": "test_r71_cap"}
    for i in range(cap + 10):
        _fire_rejection(record_fn, status, str(i))
    diag = status.get("grounding_diagnostics") or {}
    records: List[Dict[str, Any]] = diag.get("rejection_records") or []
    assert len(records) <= cap, (
        f"Round 71 / Phase 5 (#24): records list must be bounded by "
        f"_R64_MAX_GROUNDING_RECORDS={cap}; got {len(records)}."
    )


def test_round71_records_list_keeps_most_recent_after_overflow() -> None:
    """When the cap is exceeded, the MOST RECENT records MUST survive
    (FIFO trim from the head, not stop-appending at the tail)."""
    record_fn, cap = _load_record_helper()
    status: Dict[str, Any] = {"analysis_id": "test_r71_recent"}
    for i in range(cap + 10):
        _fire_rejection(record_fn, status, str(i))
    diag = status.get("grounding_diagnostics") or {}
    records: List[Dict[str, Any]] = diag.get("rejection_records") or []
    markers = [r.get("sample_offending", {}).get("marker") for r in records]
    last_marker = str(cap + 10 - 1)
    # The MOST RECENT rejection MUST be in the list.
    assert last_marker in markers, (
        f"Round 71 / Phase 5 (#24): the most recent rejection (marker={last_marker!r}) "
        f"MUST be in the trimmed records.  Got markers={markers}"
    )
    # The OLDEST rejection (marker="0") MUST have been dropped.
    assert "0" not in markers, (
        f"Round 71 / Phase 5 (#24): FIFO trim must drop the OLDEST "
        f"rejection (marker='0').  Got markers={markers}"
    )


def test_round71_summary_total_reflects_true_rejection_count() -> None:
    """The ``rejection_summary.rejected`` count MUST reflect ALL
    rejections, not just the bounded list length."""
    record_fn, cap = _load_record_helper()
    status: Dict[str, Any] = {"analysis_id": "test_r71_summary"}
    total_fired = cap + 10
    for i in range(total_fired):
        _fire_rejection(record_fn, status, str(i))
    diag = status.get("grounding_diagnostics") or {}
    summary = diag.get("rejection_summary") or {}
    assert summary.get("rejected") == total_fired, (
        f"Round 71 / Phase 5 (#24): rejection_summary.rejected must "
        f"reflect TRUE total ({total_fired}), not the bounded list length.  "
        f"Got rejected={summary.get('rejected')}."
    )
