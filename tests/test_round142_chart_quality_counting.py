"""Round 142: raster chart images count in DOCX quality inspection."""

from __future__ import annotations

import base64
import zipfile
from pathlib import Path

from docx import Document
from docx.shared import Inches

from report_iteration_loop import (
    _count_docx_chart_parts,
    _inspect_docx_visible_charts,
    evaluate_report_quality,
)


_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _write_png(path: Path) -> None:
    path.write_bytes(_ONE_PIXEL_PNG)


def _write_quality_ready_docx(path: Path) -> Document:
    doc = Document()
    doc.add_heading("Portfolio Overview", level=1)
    doc.add_heading("Operational Trends", level=2)
    doc.add_paragraph("Current portfolio observations are summarized below.")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Signal"
    table.cell(0, 1).text = "Status"
    table.cell(1, 0).text = "Adoption"
    table.cell(1, 1).text = "Stable"
    doc.save(path)
    return doc


def test_embedded_png_with_dimensions_and_alt_metadata_counts_as_visible_chart(
    tmp_path: Path,
) -> None:
    png_path = tmp_path / "action-plan-status.png"
    docx_path = tmp_path / "report-with-raster-chart.docx"
    _write_png(png_path)

    doc = _write_quality_ready_docx(docx_path)
    picture = doc.add_picture(str(png_path), width=Inches(2.5))
    picture._inline.docPr.set("title", "Action Plan Status")
    picture._inline.docPr.set(
        "descr",
        "Action plan status distribution chart based on tracked plan records.",
    )
    doc.save(docx_path)

    metadata = _inspect_docx_visible_charts(docx_path)

    assert metadata["visible_chart_count"] == 1
    assert metadata["native_chart_count"] == 0
    assert metadata["embedded_raster_chart_count"] == 1
    assert metadata["inspection_errors"] == []
    embedded = metadata["embedded_raster_charts"][0]
    assert embedded["width_emu"] > 0
    assert embedded["height_emu"] > 0
    assert embedded["title"] == "Action Plan Status"
    assert embedded["alt_text"].startswith("Action plan status distribution chart")
    assert embedded["media_part"].endswith(".png")
    assert _count_docx_chart_parts(docx_path) == 1

    payload, _gate = evaluate_report_quality(
        docx_path,
        None,
        scenario_key="comprehensive",
        strict=False,
    )
    assert payload["chart_count"] == 1
    assert payload["chart_metadata"]["embedded_raster_chart_count"] == 1


def test_document_without_chart_has_zero_visible_charts(tmp_path: Path) -> None:
    docx_path = tmp_path / "report-without-chart.docx"
    _write_quality_ready_docx(docx_path)

    metadata = _inspect_docx_visible_charts(docx_path)

    assert metadata["visible_chart_count"] == 0
    assert metadata["native_chart_count"] == 0
    assert metadata["embedded_raster_chart_count"] == 0
    assert metadata["embedded_raster_charts"] == []
    assert _count_docx_chart_parts(docx_path) == 0


def test_unlabelled_embedded_png_does_not_count_as_chart(tmp_path: Path) -> None:
    png_path = tmp_path / "decorative-image.png"
    docx_path = tmp_path / "report-with-decorative-image.docx"
    _write_png(png_path)

    doc = Document()
    doc.add_picture(str(png_path), width=Inches(1))
    doc.save(docx_path)

    metadata = _inspect_docx_visible_charts(docx_path)

    assert metadata["visible_chart_count"] == 0
    assert metadata["embedded_raster_chart_count"] == 0


def test_native_chart_parts_remain_supported(tmp_path: Path) -> None:
    docx_path = tmp_path / "report-with-native-chart-part.docx"
    _write_quality_ready_docx(docx_path)
    with zipfile.ZipFile(docx_path, mode="a") as archive:
        archive.writestr(
            "word/charts/chart1.xml",
            "<c:chartSpace xmlns:c=\"http://schemas.openxmlformats.org/drawingml/2006/chart\"/>",
        )

    metadata = _inspect_docx_visible_charts(docx_path)

    assert metadata["visible_chart_count"] == 1
    assert metadata["native_chart_count"] == 1
    assert metadata["native_chart_parts"] == ["word/charts/chart1.xml"]
    assert metadata["embedded_raster_chart_count"] == 0
    assert _count_docx_chart_parts(docx_path) == 1
