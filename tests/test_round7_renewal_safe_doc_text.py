"""Round 7 / Phase 6.12 regression test.

``advanced_renewal_analyzer`` must define and route Word headings
through a local ``_safe_doc_text`` helper (parity with Round 6 /
Phase 1.4 in ``app_simple.py`` and ``compact_report_formatter.py``)
so a control char or oversized customer name cannot crash the docx
writer or yield a Word "needs repair" prompt.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_6_12() -> None:
    src = (REPO_ROOT.joinpath("advanced_renewal_analyzer.py")).read_text(encoding="utf-8")
    assert "Round 7 / Phase 6.12" in src, (
        "Round 7 Phase 6.12 marker missing in advanced_renewal_analyzer.py."
    )
    assert "_safe_doc_text" in src, (
        "Round 7 Phase 6.12: _safe_doc_text helper missing in advanced_renewal_analyzer.py."
    )
    assert "def _safe_doc_text(" in src, (
        "Round 7 Phase 6.12: _safe_doc_text definition missing in advanced_renewal_analyzer.py."
    )
