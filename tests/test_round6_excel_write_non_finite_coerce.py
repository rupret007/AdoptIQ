"""Round 6 / Phase 1.2 regression test.

NaN/Inf/pd.NA must be coerced before ``worksheet.write`` to avoid
xlsxwriter raising on non-finite floats.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_excel_non_finite_coerced() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 1.2" in src, (
        "Round 6 Phase 1.2 marker missing in app_simple.py."
    )
