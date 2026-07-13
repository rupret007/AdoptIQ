"""Round 3 / Phase 1.1 regression test.

`create_executive_intelligence_report` accepts a ``chart_paths`` arg and
must embed every existing PNG as a picture in the saved Word document.
Previously the arg was accepted, documented, and silently dropped.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _make_chart_png(tmp_path: Path, name: str = "chart") -> str:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        pytest.skip("matplotlib unavailable in this environment")

    fig, ax = plt.subplots(figsize=(2, 2))
    ax.plot([0, 1, 2], [0, 1, 4])
    out = tmp_path / f"{name}.png"
    fig.savefig(out)
    plt.close(fig)
    return str(out)


def test_chart_paths_embedded_as_pictures(tmp_path):
    try:
        from executive_intelligence_formatter import (
            create_executive_intelligence_report,
        )
        from docx import Document
    except Exception:
        pytest.skip("executive_intelligence_formatter or python-docx unavailable")

    chart1 = _make_chart_png(tmp_path, "c1")
    chart2 = _make_chart_png(tmp_path, "c2")

    out_path = tmp_path / "ei.docx"
    ab = pd.DataFrame(
        [
            {
                "customer_name": "Acme",
                "SUBJECT_C": "x",
                "SEVERITY_C": "Critical",
                "AB_STATUS_C": "Open",
                "ID": "AB1",
            }
        ]
    )
    cs = pd.DataFrame(
        [
            {
                "customer_name": "Acme",
                "SR Number": "T1",
                "Title": "x",
                "Severity": "P1",
                "Transaction ID": "",
                "Date/Time Opened": "2026-01-01",
            }
        ]
    )

    create_executive_intelligence_report(
        analysis_id="t-ei-1",
        manager="m",
        technology="t",
        days=30,
        ab_data=ab,
        csone_data=cs,
        ai_insights={"executive_summary": "ok"},
        ext_bugs=[],
        ext_incidents=[],
        risk_scores={},
        risk_summary={},
        output_path=str(out_path),
        chart_paths=[chart1, chart2],
    )

    assert out_path.exists(), "EI report did not write output file"
    doc = Document(str(out_path))
    image_count = sum(
        1 for shape in doc.inline_shapes if shape.type is not None
    )
    assert image_count >= 2, (
        f"expected at least 2 embedded chart images, found {image_count}"
    )


def test_chart_paths_missing_files_skipped(tmp_path):
    try:
        from executive_intelligence_formatter import (
            create_executive_intelligence_report,
        )
        from docx import Document
    except Exception:
        pytest.skip("executive_intelligence_formatter or python-docx unavailable")

    chart_real = _make_chart_png(tmp_path, "real")
    chart_missing = str(tmp_path / "does_not_exist.png")

    out_path = tmp_path / "ei.docx"
    ab = pd.DataFrame()
    cs = pd.DataFrame()

    create_executive_intelligence_report(
        analysis_id="t-ei-2",
        manager="m",
        technology="t",
        days=30,
        ab_data=ab,
        csone_data=cs,
        ai_insights={},
        ext_bugs=[],
        ext_incidents=[],
        risk_scores={},
        risk_summary={},
        output_path=str(out_path),
        chart_paths=[chart_real, chart_missing],
    )

    assert out_path.exists()
    doc = Document(str(out_path))
    image_count = sum(1 for _ in doc.inline_shapes)
    assert image_count == 1
