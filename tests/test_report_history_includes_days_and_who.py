"""Round 3 / Phase 5.4 regression test.

The ``report_history`` table must include the new audit columns
(``days``, paths, hashes, and the partial-data warnings JSON) so
History rows survive a restart with full provenance, and the
``record_report_completion`` API must accept and persist them.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_report_history_table_includes_audit_columns():
    src = (PROJECT_ROOT / "enhanced_admin_dashboard_v2.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    idx = src.find("CREATE TABLE IF NOT EXISTS report_history")
    assert idx >= 0
    schema = src[idx : idx + 2000]
    for col in (
        "days",
        "word_path",
        "excel_path",
        "word_hash",
        "excel_hash",
        "partial_data_warnings_json",
    ):
        assert col in schema, f"report_history schema missing column: {col}"


def test_record_report_completion_accepts_new_audit_args():
    """Signature must accept the new args so call sites can persist
    the analysis horizon, generated artifact paths, and the warnings
    list."""
    from enhanced_admin_dashboard_v2 import record_report_completion

    sig = inspect.signature(record_report_completion)
    for name in ("days", "word_path", "excel_path", "partial_data_warnings"):
        assert name in sig.parameters, (
            f"record_report_completion is missing parameter: {name}"
        )
